from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.uploads import UploadSession
from enterprise_doc_core.uploads.service import UploadCreationService


@dataclass(frozen=True, slots=True)
class FixturePrincipal:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    token: str
    quota_bytes: int

    @property
    def context(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role=MembershipRole.OWNER.value,
        )


class TokenPrincipalResolver:
    def __init__(self, principals: list[FixturePrincipal]) -> None:
        self._principals = {principal.token: principal.context for principal in principals}

    async def resolve(self, token: str) -> PrincipalContext:
        return self._principals[token]


def _principal(*, label: str, quota_bytes: int) -> FixturePrincipal:
    return FixturePrincipal(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        membership_id=uuid4(),
        token=f"token-{label}",
        quota_bytes=quota_bytes,
    )


def _headers(principal: FixturePrincipal, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {principal.token}",
        "Idempotency-Key": key,
    }


def _body(*, filename: str, size_bytes: int) -> dict[str, object]:
    return {
        "filename": filename,
        "sizeBytes": size_bytes,
        "mediaType": "text/plain",
        "sha256": "a" * 64,
    }


async def _post(
    client: AsyncClient,
    principal: FixturePrincipal,
    key: str,
    *,
    filename: str,
    size_bytes: int,
) -> Response:
    return await client.post(
        "/api/upload-sessions",
        headers=_headers(principal, key),
        json=_body(filename=filename, size_bytes=size_bytes),
    )


@pytest.mark.integration
async def test_concurrent_create_is_idempotent_and_quota_safe() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    same = _principal(label="same", quota_bytes=1_000)
    conflict = _principal(label="conflict", quota_bytes=1_000)
    quota = _principal(label="quota", quota_bytes=100)
    revoked = _principal(label="revoked", quota_bytes=1_000)
    principals = [same, conflict, quota, revoked]

    try:
        async with session_factory.begin() as session:
            for item in principals:
                session.add(
                    Tenant(
                        id=item.tenant_id,
                        name=f"Upload Integration {item.token}",
                        slug=f"upload-{item.tenant_id}",
                        quota_bytes=item.quota_bytes,
                    )
                )
                session.add(User(id=item.actor_id, email=f"{item.actor_id}@example.test"))
            await session.flush()
            for item in principals:
                session.add(
                    Membership(
                        id=item.membership_id,
                        tenant_id=item.tenant_id,
                        user_id=item.actor_id,
                        role=MembershipRole.OWNER.value,
                    )
                )
        async with session_factory.begin() as session:
            revoked_membership = await session.get(Membership, revoked.membership_id)
            assert revoked_membership is not None
            revoked_membership.is_active = False

        app = create_app(
            settings=settings,
            checkers=[],
            principal_resolver=TokenPrincipalResolver(principals),
            upload_creation_service=UploadCreationService(
                session_factory=session_factory,
                settings=settings.upload,
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            same_results = await asyncio.gather(
                _post(client, same, "same-key", filename="same.txt", size_bytes=64),
                _post(client, same, "same-key", filename="same.txt", size_bytes=64),
            )
            conflict_results = await asyncio.gather(
                _post(
                    client,
                    conflict,
                    "conflict-key",
                    filename="first.txt",
                    size_bytes=64,
                ),
                _post(
                    client,
                    conflict,
                    "conflict-key",
                    filename="second.txt",
                    size_bytes=65,
                ),
            )
            quota_results = await asyncio.gather(
                _post(client, quota, "quota-a", filename="a.txt", size_bytes=60),
                _post(client, quota, "quota-b", filename="b.txt", size_bytes=60),
            )
            revoked_result = await _post(
                client,
                revoked,
                "revoked-key",
                filename="revoked.txt",
                size_bytes=60,
            )

        assert sorted(response.status_code for response in same_results) == [200, 201]
        assert len({response.json()["sessionId"] for response in same_results}) == 1
        assert sorted(response.status_code for response in conflict_results) == [201, 409]
        assert sorted(response.status_code for response in quota_results) == [201, 409]
        assert (
            next(
                response.json()["error"]["code"]
                for response in conflict_results
                if response.status_code == 409
            )
            == "upload_idempotency_conflict"
        )
        assert (
            next(
                response.json()["error"]["code"]
                for response in quota_results
                if response.status_code == 409
            )
            == "upload_quota_exceeded"
        )
        assert revoked_result.status_code == 404
        assert revoked_result.json()["error"]["code"] == "upload_tenant_unavailable"

        async with session_factory() as session:
            for item, expected_count in (
                (same, 1),
                (conflict, 1),
                (quota, 1),
                (revoked, 0),
            ):
                count = await session.scalar(
                    select(func.count())
                    .select_from(UploadSession)
                    .where(UploadSession.tenant_id == item.tenant_id)
                )
                tenant = await session.get(Tenant, item.tenant_id)
                assert count == expected_count
                assert tenant is not None
            same_tenant = await session.get(Tenant, same.tenant_id)
            conflict_tenant = await session.get(Tenant, conflict.tenant_id)
            quota_tenant = await session.get(Tenant, quota.tenant_id)
            revoked_tenant = await session.get(Tenant, revoked.tenant_id)
            assert same_tenant is not None and same_tenant.reserved_storage_bytes == 64
            assert conflict_tenant is not None
            assert conflict_tenant.reserved_storage_bytes in {64, 65}
            assert quota_tenant is not None and quota_tenant.reserved_storage_bytes == 60
            assert revoked_tenant is not None and revoked_tenant.reserved_storage_bytes == 0
    finally:
        async with session_factory.begin() as session:
            tenant_ids = [item.tenant_id for item in principals]
            actor_ids = [item.actor_id for item in principals]
            membership_ids = [item.membership_id for item in principals]
            await session.execute(
                delete(UploadSession).where(UploadSession.tenant_id.in_(tenant_ids))
            )
            await session.execute(delete(Membership).where(Membership.id.in_(membership_ids)))
            await session.execute(delete(User).where(User.id.in_(actor_ids)))
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await engine.dispose()
