from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus
from enterprise_doc_core.uploads.policy import build_object_key, validate_upload_metadata

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,128}$")


class UploadCreationError(Exception):
    code = "upload_creation_failed"
    message = "The upload session could not be created."

    def __init__(self) -> None:
        super().__init__(self.message)


class UploadIdempotencyKeyInvalid(UploadCreationError):
    code = "idempotency_key_invalid"
    message = "The Idempotency-Key must contain 1 to 128 visible ASCII characters."


class UploadIdempotencyConflict(UploadCreationError):
    code = "upload_idempotency_conflict"
    message = "The Idempotency-Key was already used with different upload metadata."


class UploadQuotaExceeded(UploadCreationError):
    code = "upload_quota_exceeded"
    message = "The tenant does not have enough available storage quota."


class UploadTenantUnavailable(UploadCreationError):
    code = "upload_tenant_unavailable"
    message = "The upload tenant is unavailable."


@dataclass(frozen=True, slots=True)
class CreateUploadSessionInput:
    filename: str
    size_bytes: int
    media_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class CreateUploadSessionResult:
    session_id: UUID
    status: str
    filename: str
    extension: str
    media_type: str
    size_bytes: int
    declared_sha256: str
    part_size_bytes: int
    expected_part_count: int
    expires_at: datetime
    replayed: bool


class UploadCreationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
        settings: UploadSettings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def create(
        self,
        *,
        principal: PrincipalContext,
        idempotency_key: str,
        request: CreateUploadSessionInput,
    ) -> CreateUploadSessionResult:
        if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
            raise UploadIdempotencyKeyInvalid()
        metadata = validate_upload_metadata(
            filename=request.filename,
            size_bytes=request.size_bytes,
            media_type=request.media_type,
            sha256=request.sha256,
            settings=self.settings,
        )
        try:
            tenant_id = UUID(principal.tenant_id)
            actor_id = UUID(principal.actor_id)
        except ValueError as exc:
            raise UploadTenantUnavailable() from exc
        if self.session_factory is None:
            raise RuntimeError("upload creation session factory is unavailable")

        async with self.session_factory.begin() as session:
            tenant = await session.scalar(
                select(Tenant)
                .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
                .with_for_update()
            )
            if tenant is None:
                raise UploadTenantUnavailable()
            membership_id = await session.scalar(
                select(Membership.id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == actor_id,
                    Membership.is_active.is_(True),
                    User.is_active.is_(True),
                )
                .with_for_update()
            )
            if membership_id is None:
                raise UploadTenantUnavailable()

            existing = await session.scalar(
                select(UploadSession).where(
                    UploadSession.tenant_id == tenant_id,
                    UploadSession.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.actor_id != actor_id
                    or existing.request_fingerprint != metadata.request_fingerprint
                ):
                    raise UploadIdempotencyConflict()
                return _result_from_session(existing, replayed=True)

            projected_storage = (
                tenant.used_storage_bytes + tenant.reserved_storage_bytes + metadata.size_bytes
            )
            if projected_storage > tenant.quota_bytes:
                raise UploadQuotaExceeded()

            session_id = uuid4()
            pending_document_id = uuid4()
            pending_version_id = uuid4()
            now = datetime.now(UTC)
            upload_session = UploadSession(
                id=session_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                pending_document_id=pending_document_id,
                pending_version_id=pending_version_id,
                status=UploadSessionStatus.INITIALIZING.value,
                idempotency_key=idempotency_key,
                request_fingerprint=metadata.request_fingerprint,
                object_key=build_object_key(
                    session_id=session_id,
                    version_id=pending_version_id,
                ),
                original_filename=metadata.filename,
                extension=metadata.extension,
                declared_media_type=metadata.media_type,
                size_bytes=metadata.size_bytes,
                declared_sha256=metadata.sha256,
                part_size_bytes=metadata.part_size_bytes,
                expected_part_count=metadata.part_count,
                reserved_bytes=metadata.size_bytes,
                expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
            )
            tenant.reserved_storage_bytes += metadata.size_bytes
            session.add(upload_session)
            await session.flush()
            return _result_from_session(upload_session, replayed=False)


def _result_from_session(
    upload_session: UploadSession,
    *,
    replayed: bool,
) -> CreateUploadSessionResult:
    return CreateUploadSessionResult(
        session_id=upload_session.id,
        status=upload_session.status,
        filename=upload_session.original_filename,
        extension=upload_session.extension,
        media_type=upload_session.declared_media_type,
        size_bytes=upload_session.size_bytes,
        declared_sha256=upload_session.declared_sha256,
        part_size_bytes=upload_session.part_size_bytes,
        expected_part_count=upload_session.expected_part_count,
        expires_at=upload_session.expires_at,
        replayed=replayed,
    )
