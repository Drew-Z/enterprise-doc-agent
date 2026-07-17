from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.object_store import MultipartObjectStore
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus
from enterprise_doc_core.uploads.policy import build_object_key, validate_upload_metadata

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,128}$")
_LOGGER = logging.getLogger("enterprise_doc_core.uploads")


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


class UploadInitializationFailed(UploadCreationError):
    code = "upload_initialization_failed"
    message = "The multipart upload could not be initialized."


class UploadInitializationInProgress(UploadCreationError):
    code = "upload_initialization_in_progress"
    message = "The multipart upload is still being initialized."


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


@dataclass(frozen=True, slots=True)
class _PendingUploadInitialization:
    session_id: UUID
    tenant_id: UUID
    actor_id: UUID
    pending_version_id: UUID
    object_key: str
    size_bytes: int


class UploadCreationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
        settings: UploadSettings,
        object_store: MultipartObjectStore,
        documents_bucket: str,
        initialization_wait_timeout_seconds: float = 2.0,
        initialization_poll_interval_seconds: float = 0.05,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if initialization_wait_timeout_seconds <= 0:
            raise ValueError("initialization wait timeout must be positive")
        if initialization_poll_interval_seconds <= 0:
            raise ValueError("initialization poll interval must be positive")
        self.session_factory = session_factory
        self.settings = settings
        self.object_store = object_store
        self.documents_bucket = documents_bucket
        self.initialization_wait_timeout_seconds = initialization_wait_timeout_seconds
        self.initialization_poll_interval_seconds = initialization_poll_interval_seconds
        self.monotonic_clock = monotonic_clock

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

        pending_initialization: _PendingUploadInitialization | None = None
        existing_session: UploadSession | None = None
        membership_validated = False
        try:
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
                membership_validated = True

                existing_session = await session.scalar(
                    select(UploadSession).where(
                        UploadSession.tenant_id == tenant_id,
                        UploadSession.idempotency_key == idempotency_key,
                    )
                )
                if existing_session is not None:
                    _require_matching_idempotency_session(
                        existing_session,
                        actor_id=actor_id,
                        request_fingerprint=metadata.request_fingerprint,
                    )
                else:
                    projected_storage = (
                        tenant.used_storage_bytes
                        + tenant.reserved_storage_bytes
                        + metadata.size_bytes
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
                    pending_initialization = _pending_from_session(upload_session)
        except UploadCreationError:
            raise
        except Exception as reservation_error:
            if not membership_validated:
                raise
            try:
                recovered = await self._read_idempotency_session(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                )
            except Exception:
                raise reservation_error from None
            if recovered is None:
                raise
            _require_matching_idempotency_session(
                recovered,
                actor_id=actor_id,
                request_fingerprint=metadata.request_fingerprint,
            )
            if (
                pending_initialization is not None
                and recovered.id == pending_initialization.session_id
            ):
                if recovered.status != UploadSessionStatus.INITIALIZING.value:
                    return _result_from_session(recovered, replayed=False)
            else:
                existing_session = recovered

        if existing_session is not None:
            if existing_session.status == UploadSessionStatus.INITIALIZING.value:
                return await self._wait_for_initialization(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=metadata.request_fingerprint,
                )
            return _result_from_session(existing_session, replayed=True)

        if pending_initialization is None:
            raise UploadInitializationFailed()

        try:
            upload_id = await self.object_store.create_upload(
                bucket=self.documents_bucket,
                key=pending_initialization.object_key,
                metadata={
                    "contract": "m1",
                    "upload-session-id": str(pending_initialization.session_id),
                    "version-id": str(pending_initialization.pending_version_id),
                    "declared-size": str(pending_initialization.size_bytes),
                },
            )
        except Exception:
            await self._compensate_initialization_failure(pending_initialization)
            raise

        activation_result: CreateUploadSessionResult | None = None
        try:
            async with self.session_factory.begin() as session:
                activation_session = await session.scalar(
                    select(UploadSession)
                    .where(
                        UploadSession.id == pending_initialization.session_id,
                        UploadSession.tenant_id == pending_initialization.tenant_id,
                        UploadSession.actor_id == pending_initialization.actor_id,
                    )
                    .with_for_update()
                )
                if (
                    activation_session is None
                    or activation_session.status != UploadSessionStatus.INITIALIZING.value
                    or activation_session.object_store_upload_id is not None
                ):
                    raise UploadInitializationFailed()
                activation_session.object_store_upload_id = upload_id
                activation_session.status = UploadSessionStatus.ACTIVE.value
                activation_session.last_error_code = None
                await session.flush()
                activation_result = _result_from_session(activation_session, replayed=False)
        except Exception as error:
            try:
                recovered = await self._read_session_by_id(
                    tenant_id=pending_initialization.tenant_id,
                    actor_id=pending_initialization.actor_id,
                    session_id=pending_initialization.session_id,
                )
            except Exception:
                _LOGGER.warning(
                    "upload activation outcome is unknown",
                    extra={"event_data": {"error_code": "upload_activation_outcome_unknown"}},
                )
                raise UploadInitializationFailed() from error
            if (
                recovered is not None
                and recovered.status == UploadSessionStatus.ACTIVE.value
                and recovered.object_store_upload_id == upload_id
            ):
                return _result_from_session(recovered, replayed=False)
            aborted = await self._abort_initialization_best_effort(
                pending_initialization,
                upload_id=upload_id,
            )
            if (
                recovered is not None
                and recovered.status == UploadSessionStatus.INITIALIZING.value
                and recovered.object_store_upload_id is None
            ):
                if aborted:
                    await self._compensate_initialization_failure(pending_initialization)
                else:
                    await self._preserve_failed_initialization(
                        pending_initialization,
                        upload_id=upload_id,
                    )
            raise UploadInitializationFailed() from error

        if activation_result is None:
            raise UploadInitializationFailed()
        return activation_result

    async def _wait_for_initialization(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> CreateUploadSessionResult:
        deadline = self.monotonic_clock() + self.initialization_wait_timeout_seconds
        while True:
            upload_session = await self._read_idempotency_session(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
            )
            if upload_session is None:
                raise UploadInitializationFailed()
            _require_matching_idempotency_session(
                upload_session,
                actor_id=actor_id,
                request_fingerprint=request_fingerprint,
            )
            if upload_session.status != UploadSessionStatus.INITIALIZING.value:
                return _result_from_session(upload_session, replayed=True)

            remaining = deadline - self.monotonic_clock()
            if remaining <= 0:
                raise UploadInitializationInProgress()
            await asyncio.sleep(min(self.initialization_poll_interval_seconds, remaining))

    async def _read_idempotency_session(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
    ) -> UploadSession | None:
        if self.session_factory is None:
            return None
        async with self.session_factory() as session:
            return cast(
                UploadSession | None,
                await session.scalar(
                    select(UploadSession).where(
                        UploadSession.tenant_id == tenant_id,
                        UploadSession.idempotency_key == idempotency_key,
                    )
                ),
            )

    async def _read_session_by_id(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> UploadSession | None:
        if self.session_factory is None:
            return None
        async with self.session_factory() as session:
            return cast(
                UploadSession | None,
                await session.scalar(
                    select(UploadSession).where(
                        UploadSession.id == session_id,
                        UploadSession.tenant_id == tenant_id,
                        UploadSession.actor_id == actor_id,
                    )
                ),
            )

    async def _compensate_initialization_failure(
        self,
        pending: _PendingUploadInitialization,
    ) -> None:
        if self.session_factory is None:
            return
        try:
            async with self.session_factory.begin() as session:
                upload_session = await session.scalar(
                    select(UploadSession)
                    .where(
                        UploadSession.id == pending.session_id,
                        UploadSession.tenant_id == pending.tenant_id,
                        UploadSession.actor_id == pending.actor_id,
                    )
                    .with_for_update()
                )
                if (
                    upload_session is None
                    or upload_session.status != UploadSessionStatus.INITIALIZING.value
                    or upload_session.object_store_upload_id is not None
                ):
                    return
                tenant = await session.scalar(
                    select(Tenant).where(Tenant.id == pending.tenant_id).with_for_update()
                )
                if tenant is None or tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                    raise UploadInitializationFailed()
                tenant.reserved_storage_bytes -= upload_session.reserved_bytes
                await session.delete(upload_session)
        except Exception:
            _LOGGER.warning(
                "upload initialization compensation failed",
                extra={"event_data": {"error_code": "upload_initialization_compensation_failed"}},
            )

    async def _preserve_failed_initialization(
        self,
        pending: _PendingUploadInitialization,
        *,
        upload_id: str,
    ) -> None:
        if self.session_factory is None:
            return
        try:
            async with self.session_factory.begin() as session:
                upload_session = await session.scalar(
                    select(UploadSession)
                    .where(
                        UploadSession.id == pending.session_id,
                        UploadSession.tenant_id == pending.tenant_id,
                        UploadSession.actor_id == pending.actor_id,
                    )
                    .with_for_update()
                )
                if (
                    upload_session is None
                    or upload_session.status != UploadSessionStatus.INITIALIZING.value
                    or upload_session.object_store_upload_id is not None
                ):
                    return
                tenant = await session.scalar(
                    select(Tenant).where(Tenant.id == pending.tenant_id).with_for_update()
                )
                if tenant is None or tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                    raise UploadInitializationFailed()
                tenant.reserved_storage_bytes -= upload_session.reserved_bytes
                upload_session.reserved_bytes = 0
                upload_session.object_store_upload_id = upload_id
                upload_session.status = UploadSessionStatus.FAILED.value
                upload_session.last_error_code = "upload_initialization_abort_failed"
        except Exception:
            _LOGGER.warning(
                "upload initialization failure could not be preserved",
                extra={
                    "event_data": {
                        "error_code": "upload_initialization_failure_preservation_failed"
                    }
                },
            )

    async def _abort_initialization_best_effort(
        self,
        pending: _PendingUploadInitialization,
        *,
        upload_id: str,
    ) -> bool:
        try:
            await self.object_store.abort_upload(
                bucket=self.documents_bucket,
                key=pending.object_key,
                upload_id=upload_id,
            )
        except Exception:
            _LOGGER.warning(
                "multipart upload abort after activation failure failed",
                extra={"event_data": {"error_code": "upload_initialization_abort_failed"}},
            )
            return False
        return True


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


def _pending_from_session(upload_session: UploadSession) -> _PendingUploadInitialization:
    return _PendingUploadInitialization(
        session_id=upload_session.id,
        tenant_id=upload_session.tenant_id,
        actor_id=upload_session.actor_id,
        pending_version_id=upload_session.pending_version_id,
        object_key=upload_session.object_key,
        size_bytes=upload_session.size_bytes,
    )


def _require_matching_idempotency_session(
    upload_session: UploadSession,
    *,
    actor_id: UUID,
    request_fingerprint: str,
) -> None:
    if (
        upload_session.actor_id != actor_id
        or upload_session.request_fingerprint != request_fingerprint
    ):
        raise UploadIdempotencyConflict()
