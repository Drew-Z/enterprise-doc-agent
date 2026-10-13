from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.service import UploadCreationService
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.multipart.test_upload_create_integration import (
    CountingObjectStore,
    FixturePrincipal,
    TokenPrincipalResolver,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def upload_app(browser_db):
    principal = FixturePrincipal(uuid4(), uuid4(), uuid4(), "upload-owner", 100)
    sessions = browser_db.sessions
    async with sessions.begin() as session:
        session.add(
            Tenant(
                id=principal.tenant_id,
                name="Upload lock test",
                slug=str(principal.tenant_id),
                quota_bytes=100,
            )
        )
        session.add(User(id=principal.actor_id, email=f"{principal.actor_id}@example.test"))
        await session.flush()
        session.add(
            Membership(
                id=principal.membership_id,
                tenant_id=principal.tenant_id,
                user_id=principal.actor_id,
                role=MembershipRole.OWNER.value,
            )
        )
    settings = ApiSettings(_env_file=None, upload=UploadSettings(single_put_enabled=True))
    store = CountingObjectStore()
    service = UploadCreationService(
        session_factory=sessions,
        settings=settings.upload,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
    )
    app = create_app(
        settings=settings,
        checkers=[],
        principal_resolver=TokenPrincipalResolver([principal]),
        upload_creation_service=service,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            yield SimpleNamespace(
                client=client, sessions=sessions, principal=principal, store=store
            )


async def create_upload(b, key, transport):
    return await b.client.post(
        "/api/upload-sessions",
        headers={"Authorization": "Bearer upload-owner", "Idempotency-Key": key},
        json={
            "filename": "lock.txt",
            "sizeBytes": 64,
            "mediaType": "text/plain",
            "sha256": "a" * 64,
            "transport": transport,
        },
    )


@pytest.mark.parametrize("transport", ["multipart", "single_put"])
async def test_create_and_replay_do_not_wait_for_unrelated_audit_insert(upload_app, transport):
    b = upload_app
    async with b.sessions.begin() as writer:
        # A real insert holds FK KEY SHARE locks on both tenant and actor.
        await append_audit_event(
            writer,
            tenant_id=b.principal.tenant_id,
            actor_id=b.principal.actor_id,
            action="integration.unrelated",
            resource_type="integration",
        )
        async with asyncio.timeout(2):
            created = await create_upload(b, "same", transport)
            replay = await create_upload(b, "same", transport)
        assert created.status_code == 201, created.text
        assert replay.status_code == 200, replay.text
        assert created.json()["sessionId"] == replay.json()["sessionId"]
    async with b.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UploadSession)) == 1
        tenant = await session.get(Tenant, b.principal.tenant_id)
        assert tenant.reserved_storage_bytes == 64
    assert len(b.store.created) == (1 if transport == "multipart" else 0)


@pytest.mark.parametrize("entity", ["tenant", "user", "membership"])
async def test_creation_waits_for_deactivation_and_rechecks_access(upload_app, entity):
    b = upload_app
    model, identifier = {
        "tenant": (Tenant, b.principal.tenant_id),
        "user": (User, b.principal.actor_id),
        "membership": (Membership, b.principal.membership_id),
    }[entity]
    request = None
    try:
        async with b.sessions.begin() as writer:
            blocker = await writer.scalar(text("SELECT pg_backend_pid()"))
            record = await writer.get(model, identifier)
            record.is_active = False
            await writer.flush()
            request = asyncio.create_task(create_upload(b, "deactivate", "single_put"))
            async with asyncio.timeout(2), b.sessions() as observer:
                while not await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)) "
                        "AND datname=current_database()"
                    ),
                    {"blocker": blocker},
                ):
                    assert not request.done(), "Upload creation bypassed the identity update"
                    await asyncio.sleep(0.01)
        async with asyncio.timeout(2):
            response = await request
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "upload_tenant_unavailable"
    finally:
        if request is not None:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    async with b.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UploadSession)) == 0
        assert (await session.get(Tenant, b.principal.tenant_id)).reserved_storage_bytes == 0
    assert not b.store.created


@pytest.mark.parametrize("transport", ["multipart", "single_put"])
async def test_creation_still_serializes_competing_last_quota(upload_app, transport):
    b = upload_app
    responses = await asyncio.gather(
        create_upload(b, "first", transport), create_upload(b, "second", transport)
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "upload_quota_exceeded"
    async with b.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UploadSession)) == 1
        assert (await session.get(Tenant, b.principal.tenant_id)).reserved_storage_bytes == 64
    assert len(b.store.created) == (1 if transport == "multipart" else 0)
