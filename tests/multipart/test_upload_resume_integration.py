from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.object_store import Boto3MultipartObjectStore, ObjectStoreError
from enterprise_doc_core.uploads import UploadPart, UploadSession
from enterprise_doc_core.uploads.service import UploadCreationService
from enterprise_doc_core.uploads.session_service import UploadSessionService

MIB = 1024**2


@dataclass(frozen=True, slots=True)
class FixturePrincipal:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    token: str

    @property
    def context(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role=MembershipRole.OWNER.value,
        )


class TokenPrincipalResolver:
    def __init__(self, principals: list[FixturePrincipal]) -> None:
        self.principals = {principal.token: principal.context for principal in principals}

    async def resolve(self, token: str) -> PrincipalContext:
        return self.principals[token]


def _principal(label: str, *, tenant_id: UUID | None = None) -> FixturePrincipal:
    return FixturePrincipal(
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        actor_id=uuid4(),
        membership_id=uuid4(),
        token=f"resume-{label}",
    )


def _checksum(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def _auth(principal: FixturePrincipal) -> dict[str, str]:
    return {"Authorization": f"Bearer {principal.token}"}


@pytest.mark.integration
async def test_real_minio_presign_resume_reconciliation_and_owner_boundary() -> None:
    settings = ApiSettings(
        _env_file=None,
        upload={"preferred_part_size_bytes": 5 * MIB},
    )
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    owner = _principal("owner")
    same_tenant_intruder = _principal("intruder", tenant_id=owner.tenant_id)
    other_tenant = _principal("other")
    principals = [owner, same_tenant_intruder, other_tenant]
    object_store = Boto3MultipartObjectStore(settings=settings.object_store)
    creation_service = UploadCreationService(
        session_factory=session_factory,
        settings=settings.upload,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
    )
    session_service = UploadSessionService(
        session_factory=session_factory,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
    )
    first = b"a" * (5 * MIB)
    second = b"tail" * 1024
    content = first + second
    session_id: UUID | None = None

    try:
        async with session_factory.begin() as database:
            for principal in (owner, other_tenant):
                database.add(
                    Tenant(
                        id=principal.tenant_id,
                        name=f"Resume {principal.token}",
                        slug=f"resume-{principal.tenant_id}",
                        quota_bytes=20 * MIB,
                    )
                )
            for principal in principals:
                database.add(
                    User(
                        id=principal.actor_id,
                        email=f"{principal.actor_id}@example.test",
                    )
                )
            await database.flush()
            for principal in principals:
                database.add(
                    Membership(
                        id=principal.membership_id,
                        tenant_id=principal.tenant_id,
                        user_id=principal.actor_id,
                        role=MembershipRole.OWNER.value,
                    )
                )

        app = create_app(
            settings=settings,
            checkers=[],
            principal_resolver=TokenPrincipalResolver(principals),
            upload_creation_service=creation_service,
            upload_session_service=session_service,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as api:
            created = await api.post(
                "/api/upload-sessions",
                headers={**_auth(owner), "Idempotency-Key": "resume-real-minio"},
                json={
                    "filename": "resume.txt",
                    "sizeBytes": len(content),
                    "mediaType": "text/plain",
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            assert created.status_code == 201
            assert created.json()["status"] == "active"
            assert created.json()["expectedPartCount"] == 2
            session_id = UUID(created.json()["sessionId"])
            async with session_factory.begin() as database:
                upload_session = await database.get(UploadSession, session_id)
                assert upload_session is not None
                upload_session.expires_at = datetime.now(UTC) + timedelta(seconds=300)
            part_one_path = f"/api/upload-sessions/{session_id}/parts/1/presign"

            repeated = await asyncio.gather(
                api.post(
                    part_one_path,
                    headers=_auth(owner),
                    json={"sizeBytes": len(first), "checksumSha256": _checksum(first)},
                ),
                api.post(
                    part_one_path,
                    headers=_auth(owner),
                    json={"sizeBytes": len(first), "checksumSha256": _checksum(first)},
                ),
            )
            assert [response.status_code for response in repeated] == [200, 200]
            first_signed = repeated[0].json()
            assert 1 <= first_signed["expiresInSeconds"] <= 300
            assert first_signed["expiresInSeconds"] < settings.object_store.presign_ttl_seconds
            assert {
                (
                    response.json()["sizeBytes"],
                    response.json()["checksumSha256"],
                    tuple(sorted(response.json()["headers"].items())),
                )
                for response in repeated
            } == {
                (
                    len(first),
                    _checksum(first),
                    (("x-amz-checksum-sha256", _checksum(first)),),
                )
            }
            async with session_factory() as database:
                expectations = (
                    await database.scalars(
                        select(UploadPart).where(
                            UploadPart.upload_session_id == session_id,
                            UploadPart.part_number == 1,
                        )
                    )
                ).all()
                assert len(expectations) == 1
                assert expectations[0].expected_checksum_sha256 == _checksum(first)

            conflict = await api.post(
                part_one_path,
                headers=_auth(owner),
                json={"sizeBytes": len(first), "checksumSha256": _checksum(b"b" * len(first))},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "upload_part_checksum_conflict"

            wrong_size = await api.post(
                f"/api/upload-sessions/{session_id}/parts/2/presign",
                headers=_auth(owner),
                json={"sizeBytes": len(second) + 1, "checksumSha256": _checksum(second)},
            )
            invalid_number = await api.post(
                f"/api/upload-sessions/{session_id}/parts/3/presign",
                headers=_auth(owner),
                json={"sizeBytes": len(second), "checksumSha256": _checksum(second)},
            )
            malformed_checksum = await api.post(
                part_one_path,
                headers=_auth(owner),
                json={"sizeBytes": len(first), "checksumSha256": "not-base64"},
            )
            assert wrong_size.status_code == 400
            assert wrong_size.json()["error"]["code"] == "upload_part_size_invalid"
            assert invalid_number.status_code == 400
            assert invalid_number.json()["error"]["code"] == "upload_part_number_invalid"
            assert malformed_checksum.status_code == 400
            assert malformed_checksum.json()["error"]["code"] == "upload_part_checksum_invalid"

            for principal in (same_tenant_intruder, other_tenant):
                hidden = await api.get(
                    f"/api/upload-sessions/{session_id}",
                    headers=_auth(principal),
                )
                assert hidden.status_code == 404
                assert hidden.json()["error"]["code"] == "upload_session_not_found"

            async with httpx.AsyncClient(timeout=30, trust_env=False) as transfer:
                first_put = await transfer.put(
                    first_signed["url"],
                    headers=first_signed["headers"],
                    content=first,
                )
            assert first_put.status_code == 200

            after_first = await api.get(
                f"/api/upload-sessions/{session_id}",
                headers=_auth(owner),
            )
            assert after_first.status_code == 200
            assert [part["partNumber"] for part in after_first.json()["uploadedParts"]] == [1]

            second_signed = await api.post(
                f"/api/upload-sessions/{session_id}/parts/2/presign",
                headers=_auth(owner),
                json={"sizeBytes": len(second), "checksumSha256": _checksum(second)},
            )
            assert second_signed.status_code == 200
            async with httpx.AsyncClient(timeout=30, trust_env=False) as transfer:
                second_put = await transfer.put(
                    second_signed.json()["url"],
                    headers=second_signed.json()["headers"],
                    content=second,
                )
            assert second_put.status_code == 200

            resumed = await api.get(
                f"/api/upload-sessions/{session_id}",
                headers=_auth(owner),
            )
            assert resumed.status_code == 200
            assert [part["partNumber"] for part in resumed.json()["uploadedParts"]] == [1, 2]
            assert [part["sizeBytes"] for part in resumed.json()["uploadedParts"]] == [
                len(first),
                len(second),
            ]

            async with session_factory.begin() as database:
                upload_session = await database.get(UploadSession, session_id)
                assert upload_session is not None
                upload_session.expires_at = datetime.now(UTC) + timedelta(milliseconds=500)
            too_close_to_expiry = await api.post(
                part_one_path,
                headers=_auth(owner),
                json={"sizeBytes": len(first), "checksumSha256": _checksum(first)},
            )
            assert too_close_to_expiry.status_code == 410
            assert too_close_to_expiry.json()["error"]["code"] == "upload_session_expired"

            async with session_factory.begin() as database:
                upload_session = await database.get(UploadSession, session_id)
                assert upload_session is not None
                upload_session.expires_at = datetime.now(UTC)
            expired_get = await api.get(
                f"/api/upload-sessions/{session_id}",
                headers=_auth(owner),
            )
            expired = await api.post(
                part_one_path,
                headers=_auth(owner),
                json={"sizeBytes": len(first), "checksumSha256": _checksum(first)},
            )
            assert expired_get.status_code == 410
            assert expired_get.json()["error"]["code"] == "upload_session_expired"
            assert expired.status_code == 410
            assert expired.json()["error"]["code"] == "upload_session_expired"

        async with session_factory() as database:
            part_count = await database.scalar(
                select(func.count())
                .select_from(UploadPart)
                .where(UploadPart.upload_session_id == session_id)
            )
            verified_count = await database.scalar(
                select(func.count())
                .select_from(UploadPart)
                .where(
                    UploadPart.upload_session_id == session_id,
                    UploadPart.verified_at.is_not(None),
                )
            )
            assert part_count == 2
            assert verified_count == 2
    finally:
        if session_id is not None:
            async with session_factory() as database:
                upload_session = await database.get(UploadSession, session_id)
                if upload_session is not None and upload_session.object_store_upload_id is not None:
                    try:
                        await object_store.abort_upload(
                            bucket=settings.object_store.documents_bucket,
                            key=upload_session.object_key,
                            upload_id=upload_session.object_store_upload_id,
                        )
                    except ObjectStoreError:
                        pass
        async with session_factory.begin() as database:
            tenant_ids = list({principal.tenant_id for principal in principals})
            actor_ids = [principal.actor_id for principal in principals]
            membership_ids = [principal.membership_id for principal in principals]
            await database.execute(
                delete(UploadSession).where(UploadSession.tenant_id.in_(tenant_ids))
            )
            await database.execute(delete(Membership).where(Membership.id.in_(membership_ids)))
            await database.execute(delete(User).where(User.id.in_(actor_ids)))
            await database.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await object_store.close()
        await engine.dispose()
