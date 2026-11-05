from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.demo.models import DemoWorkspace
from enterprise_doc_core.demo.settings import UPLOAD_LIMIT, DemoError
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.uploads.creation_records import insert_upload_reservation
from enterprise_doc_core.uploads.models import UploadSession, UploadTransport
from enterprise_doc_core.uploads.service import CreateUploadSessionInput, UploadCreationService
from tests.multipart.test_upload_create_integration import CountingObjectStore


@asynccontextmanager
async def _fixture() -> AsyncIterator[
    tuple[ApiSettings, AsyncEngine, async_sessionmaker[AsyncSession], PrincipalContext]
]:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    sessions = create_session_factory(engine)
    tenant_id, actor_id = uuid4(), uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
    try:
        async with sessions.begin() as session:
            session.add(
                Tenant(id=tenant_id, name="Creation batch", slug=str(tenant_id), quota_bytes=1000)
            )
            session.add(User(id=actor_id, email=f"{actor_id}@example.test"))
            await session.flush()
            session.add(Membership(tenant_id=tenant_id, user_id=actor_id, role="owner"))
        yield settings, engine, sessions, principal
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(DemoWorkspace).where(DemoWorkspace.tenant_id == tenant_id))
            await session.execute(delete(UploadSession).where(UploadSession.tenant_id == tenant_id))
            await session.execute(delete(Membership).where(Membership.tenant_id == tenant_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


def _service(
    settings: ApiSettings, sessions: async_sessionmaker[AsyncSession]
) -> UploadCreationService:
    return UploadCreationService(
        session_factory=sessions,
        settings=settings.upload.model_copy(update={"single_put_enabled": True}),
        object_store=CountingObjectStore(),
        documents_bucket=settings.object_store.documents_bucket,
    )


def _request() -> CreateUploadSessionInput:
    return CreateUploadSessionInput(
        filename="batch.txt",
        size_bytes=64,
        media_type="text/plain",
        sha256="a" * 64,
        transport=UploadTransport.SINGLE_PUT,
    )


@pytest.mark.integration
async def test_single_put_creation_uses_four_statements_and_replay_reserves_once() -> None:
    async with _fixture() as (settings, engine, sessions, principal):
        # Establish the connection before counting business statements.
        async with sessions() as session:
            await session.execute(select(1))
        statements: list[str] = []

        def count(*args: object) -> None:
            statements.append(str(args[2]))

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            service = _service(settings, sessions)
            created = await service.create(
                principal=principal, idempotency_key="new", request=_request()
            )
            new_statement_count = len(statements)
            replay = await service.create(
                principal=principal, idempotency_key="new", request=_request()
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        assert created.session_id == replay.session_id
        assert not created.replayed and replay.replayed
        async with sessions() as session:
            tenant = await session.get(Tenant, UUID(principal.tenant_id))
            assert tenant is not None and tenant.reserved_storage_bytes == 64
            assert (
                len(
                    (
                        await session.scalars(
                            select(UploadSession).where(UploadSession.tenant_id == tenant.id)
                        )
                    ).all()
                )
                == 1
            )
        assert new_statement_count == 4


@pytest.mark.integration
async def test_failed_insert_rolls_back_the_combined_quota_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _fixture() as (settings, _engine, sessions, principal):
        service = _service(settings, sessions)
        created = await service.create(
            principal=principal, idempotency_key="first", request=_request()
        )
        monkeypatch.setattr("enterprise_doc_core.uploads.service.uuid4", lambda: created.session_id)
        with pytest.raises(IntegrityError):
            await service.create(
                principal=principal, idempotency_key="duplicate-id", request=_request()
            )
        async with sessions() as session:
            tenant = await session.get(Tenant, UUID(principal.tenant_id))
            assert tenant is not None and tenant.reserved_storage_bytes == 64
            assert (
                len(
                    (
                        await session.scalars(
                            select(UploadSession).where(UploadSession.tenant_id == tenant.id)
                        )
                    ).all()
                )
                == 1
            )


@pytest.mark.integration
@pytest.mark.parametrize("invalid", ["expired", "revoked", "cleaned"])
async def test_prefetched_demo_expiry_rejects_new_upload_but_keeps_existing_replay(
    invalid: str,
) -> None:
    async with _fixture() as (settings, _engine, sessions, principal):
        service = _service(settings, sessions)
        created = await service.create(
            principal=principal, idempotency_key="first", request=_request()
        )
        now = datetime.now(UTC)
        async with sessions.begin() as session:
            session.add(
                DemoWorkspace(
                    tenant_id=UUID(principal.tenant_id),
                    actor_id=UUID(principal.actor_id),
                    created_at=now - timedelta(hours=1),
                    expires_at=now - timedelta(seconds=1)
                    if invalid == "expired"
                    else now + timedelta(hours=1),
                    revoked_at=now if invalid == "revoked" else None,
                    cleaned_at=now if invalid == "cleaned" else None,
                    daily_attempt_limit=100,
                    daily_workspace_limit=100,
                )
            )
        with pytest.raises(DemoError) as failure:
            await service.create(principal=principal, idempotency_key="new", request=_request())
        assert failure.value.code == "demo_session_expired"
        replay = await service.create(
            principal=principal, idempotency_key="first", request=_request()
        )
        assert replay.replayed and replay.session_id == created.session_id
        async with sessions() as session:
            tenant = await session.get(Tenant, UUID(principal.tenant_id))
            assert tenant is not None and tenant.reserved_storage_bytes == 64


@pytest.mark.integration
async def test_prefetched_demo_upload_count_still_applies() -> None:
    async with _fixture() as (settings, _engine, sessions, principal):
        service = _service(settings, sessions)
        now = datetime.now(UTC)
        async with sessions.begin() as session:
            session.add(
                DemoWorkspace(
                    tenant_id=UUID(principal.tenant_id),
                    actor_id=UUID(principal.actor_id),
                    created_at=now - timedelta(seconds=1),
                    expires_at=now + timedelta(hours=1),
                    daily_attempt_limit=100,
                    daily_workspace_limit=100,
                )
            )
        for number in range(UPLOAD_LIMIT):
            await service.create(
                principal=principal, idempotency_key=f"demo-{number}", request=_request()
            )
        with pytest.raises(DemoError) as failure:
            await service.create(principal=principal, idempotency_key="excess", request=_request())
        assert failure.value.code == "demo_upload_limit"
        async with sessions() as session:
            tenant = await session.get(Tenant, UUID(principal.tenant_id))
            assert tenant is not None and tenant.reserved_storage_bytes == 64 * UPLOAD_LIMIT


@pytest.mark.integration
async def test_reservation_writer_flushes_pending_changes_and_preserves_caller_rollback() -> None:
    async with _fixture() as (_settings, engine, sessions, principal):
        scoped = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        def pending(key: str) -> UploadSession:
            return UploadSession(
                id=uuid4(),
                tenant_id=UUID(principal.tenant_id),
                actor_id=UUID(principal.actor_id),
                pending_document_id=uuid4(),
                pending_version_id=uuid4(),
                status="active",
                transport="single_put",
                idempotency_key=key,
                request_fingerprint="a" * 64,
                object_key=f"test/{key}",
                original_filename="pending.txt",
                extension="txt",
                declared_media_type="text/plain",
                size_bytes=64,
                declared_sha256="a" * 64,
                part_size_bytes=64,
                expected_part_count=1,
                reserved_bytes=64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        async with scoped.begin() as session:
            tenant = await session.scalar(
                select(Tenant)
                .where(Tenant.id == UUID(principal.tenant_id))
                .with_for_update(key_share=True)
            )
            assert tenant is not None
            tenant.quota_bytes = 800
            first = await insert_upload_reservation(session, tenant=tenant, upload=pending("first"))
            second = await insert_upload_reservation(
                session, tenant=tenant, upload=pending("second")
            )
            assert first.id != second.id and tenant.reserved_storage_bytes == 128
            assert tenant.updated_at is not None and not session.is_modified(tenant)
        with pytest.raises(RuntimeError, match="caller rollback"):
            async with scoped.begin() as session:
                tenant = await session.scalar(
                    select(Tenant)
                    .where(Tenant.id == UUID(principal.tenant_id))
                    .with_for_update(key_share=True)
                )
                assert tenant is not None
                await insert_upload_reservation(
                    session, tenant=tenant, upload=pending("rolled-back")
                )
                raise RuntimeError("caller rollback")
        async with sessions() as session:
            tenant = await session.get(Tenant, UUID(principal.tenant_id))
            assert tenant is not None and tenant.quota_bytes == 800
            assert tenant.reserved_storage_bytes == 128
            assert (
                len(
                    (
                        await session.scalars(
                            select(UploadSession).where(UploadSession.tenant_id == tenant.id)
                        )
                    ).all()
                )
                == 2
            )
