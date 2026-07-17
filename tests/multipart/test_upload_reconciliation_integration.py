from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.object_store import UploadedPart
from enterprise_doc_core.uploads import (
    UploadPart,
    UploadSession,
    UploadSessionNotActive,
    UploadSessionService,
)


class StaticListObjectStore:
    def __init__(self, parts: tuple[UploadedPart, ...]) -> None:
        self.parts = parts

    async def list_parts(self, **_: object) -> tuple[UploadedPart, ...]:
        return self.parts


class AbortingListObjectStore(StaticListObjectStore):
    def __init__(self, *, parts: tuple[UploadedPart, ...], session_factory, session_id) -> None:
        super().__init__(parts)
        self.session_factory = session_factory
        self.session_id = session_id

    async def list_parts(self, **_: object) -> tuple[UploadedPart, ...]:
        async with self.session_factory.begin() as database:
            upload_session = await database.get(UploadSession, self.session_id)
            assert upload_session is not None
            upload_session.status = "aborted"
        return self.parts


class RacingListObjectStore:
    def __init__(
        self,
        *,
        older_parts: tuple[UploadedPart, ...],
        newer_parts: tuple[UploadedPart, ...],
    ) -> None:
        self.older_parts = older_parts
        self.newer_parts = newer_parts
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def list_parts(self, **_: object) -> tuple[UploadedPart, ...]:
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
            return self.older_parts
        return self.newer_parts


@pytest.mark.integration
async def test_reconciliation_returns_only_verified_parts_and_rechecks_session_state() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    tenant_id = uuid4()
    actor_id = uuid4()
    membership_id = uuid4()
    session_id = uuid4()
    checksums = ("checksum-one", "checksum-two", "checksum-three")
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        role=MembershipRole.OWNER.value,
    )
    listed_parts = (
        UploadedPart(1, 5, '"etag-one"', checksums[0]),
        UploadedPart(2, 5, '"etag-two"', "wrong-checksum"),
        UploadedPart(3, 2, '"etag-three"', checksums[2]),
        UploadedPart(4, 1, '"unknown"', "unknown-checksum"),
    )

    try:
        async with session_factory.begin() as database:
            database.add(
                Tenant(
                    id=tenant_id,
                    name="Reconciliation",
                    slug=f"reconciliation-{tenant_id}",
                    quota_bytes=100,
                    reserved_storage_bytes=11,
                )
            )
            database.add(User(id=actor_id, email=f"{actor_id}@example.test"))
            await database.flush()
            database.add(
                Membership(
                    id=membership_id,
                    tenant_id=tenant_id,
                    user_id=actor_id,
                    role=MembershipRole.OWNER.value,
                )
            )
            database.add(
                UploadSession(
                    id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    pending_document_id=uuid4(),
                    pending_version_id=uuid4(),
                    status="active",
                    idempotency_key="reconciliation",
                    request_fingerprint="a" * 64,
                    object_key=f"m1/uploads/{uuid4().hex}/{uuid4().hex}",
                    object_store_upload_id="upload-reconciliation",
                    original_filename="reconciliation.txt",
                    extension=".txt",
                    declared_media_type="text/plain",
                    size_bytes=11,
                    declared_sha256="b" * 64,
                    part_size_bytes=5,
                    expected_part_count=3,
                    reserved_bytes=11,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await database.flush()
            previously_verified_at = datetime.now(UTC) - timedelta(minutes=1)
            for part_number, checksum in enumerate(checksums, start=1):
                database.add(
                    UploadPart(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        upload_session_id=session_id,
                        part_number=part_number,
                        expected_checksum_sha256=checksum,
                        observed_checksum_sha256=checksum,
                        etag=f'"old-etag-{part_number}"',
                        size_bytes=5 if part_number < 3 else 1,
                        verified_at=previously_verified_at,
                    )
                )

        service = UploadSessionService(
            session_factory=session_factory,
            object_store=StaticListObjectStore(listed_parts),
            documents_bucket="documents",
        )
        result = await service.get(principal=principal, session_id=session_id)

        assert [part.part_number for part in result.uploaded_parts] == [1]
        async with session_factory() as database:
            parts = (
                await database.scalars(
                    select(UploadPart)
                    .where(UploadPart.upload_session_id == session_id)
                    .order_by(UploadPart.part_number)
                )
            ).all()
            assert parts[0].verified_at is not None
            assert parts[1].verified_at is None
            assert parts[2].verified_at is None
            assert parts[1].observed_checksum_sha256 is None
            assert parts[1].etag is None
            assert parts[1].size_bytes is None
            assert parts[2].observed_checksum_sha256 is None
            assert parts[2].etag is None
            assert parts[2].size_bytes is None

        missing_result = await UploadSessionService(
            session_factory=session_factory,
            object_store=StaticListObjectStore(()),
            documents_bucket="documents",
        ).get(principal=principal, session_id=session_id)
        assert [part.part_number for part in missing_result.uploaded_parts] == [1]

        fixed_observation_time = datetime.now(UTC) + timedelta(minutes=1)
        same_clock_store = StaticListObjectStore(
            (UploadedPart(1, 5, '"etag-same-clock-old"', checksums[0]),)
        )
        same_clock_service = UploadSessionService(
            session_factory=session_factory,
            object_store=same_clock_store,
            documents_bucket="documents",
            clock=lambda: fixed_observation_time,
        )
        first_same_clock = await same_clock_service.get(
            principal=principal,
            session_id=session_id,
        )
        assert [part.etag for part in first_same_clock.uploaded_parts] == ['"etag-same-clock-old"']
        same_clock_store.parts = (UploadedPart(1, 5, '"etag-same-clock-new"', checksums[0]),)
        second_same_clock = await same_clock_service.get(
            principal=principal,
            session_id=session_id,
        )
        assert [part.etag for part in second_same_clock.uploaded_parts] == ['"etag-same-clock-new"']

        racing_store = RacingListObjectStore(
            older_parts=(UploadedPart(1, 5, '"etag-older"', checksums[0]),),
            newer_parts=(UploadedPart(1, 5, '"etag-newer"', checksums[0]),),
        )
        racing_service = UploadSessionService(
            session_factory=session_factory,
            object_store=racing_store,
            documents_bucket="documents",
            clock=lambda: fixed_observation_time,
        )
        older_task = asyncio.create_task(
            racing_service.get(principal=principal, session_id=session_id)
        )
        await racing_store.first_started.wait()
        try:
            newer_result = await racing_service.get(principal=principal, session_id=session_id)
        finally:
            racing_store.release_first.set()
        older_result = await older_task
        assert [part.etag for part in newer_result.uploaded_parts] == ['"etag-newer"']
        assert [part.etag for part in older_result.uploaded_parts] == ['"etag-newer"']

        async with session_factory() as database:
            first_part = await database.get(UploadPart, parts[0].id)
            assert first_part is not None and first_part.etag == '"etag-newer"'
            assert first_part.observation_version is not None

        async with session_factory.begin() as database:
            first_part = await database.get(UploadPart, parts[0].id)
            upload_session = await database.get(UploadSession, session_id)
            assert first_part is not None and upload_session is not None
            first_part.observed_checksum_sha256 = None
            first_part.etag = None
            first_part.size_bytes = None
            first_part.verified_at = None
            upload_session.status = "active"

        racing_service = UploadSessionService(
            session_factory=session_factory,
            object_store=AbortingListObjectStore(
                parts=(listed_parts[0],),
                session_factory=session_factory,
                session_id=session_id,
            ),
            documents_bucket="documents",
        )
        with pytest.raises(UploadSessionNotActive):
            await racing_service.get(principal=principal, session_id=session_id)

        async with session_factory() as database:
            first_part = await database.get(UploadPart, parts[0].id)
            assert first_part is not None and first_part.verified_at is None
    finally:
        async with session_factory.begin() as database:
            await database.execute(delete(UploadSession).where(UploadSession.id == session_id))
            await database.execute(delete(Membership).where(Membership.id == membership_id))
            await database.execute(delete(User).where(User.id == actor_id))
            await database.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()
