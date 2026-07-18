from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import (
    Document,
    DocumentEnvelopeViolation,
    DocumentVersion,
    DocumentVersionStatus,
    validate_document_envelope,
)
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.jobs import create_job_records
from enterprise_doc_core.object_store import (
    CompletedMultipartUpload,
    MultipartObjectStore,
    MultipartUploadNotFound,
    ObjectHead,
    ObjectStoreNotFound,
    UploadedPart,
)
from enterprise_doc_core.uploads.models import (
    UPLOAD_PART_OBSERVATION_VERSION_SEQUENCE,
    UploadPart,
    UploadSession,
    UploadSessionStatus,
)

_LOGGER = logging.getLogger("enterprise_doc_core.uploads")


class UploadSessionError(Exception):
    code = "upload_session_error"
    message = "The upload session operation failed."

    def __init__(self) -> None:
        super().__init__(self.message)


class UploadSessionNotFound(UploadSessionError):
    code = "upload_session_not_found"
    message = "The upload session was not found."


class UploadSessionNotActive(UploadSessionError):
    code = "upload_session_not_active"
    message = "The upload session is not active."


class UploadSessionExpired(UploadSessionError):
    code = "upload_session_expired"
    message = "The upload session has expired."


class UploadPartNumberInvalid(UploadSessionError):
    code = "upload_part_number_invalid"
    message = "The part number is outside the upload plan."


class UploadPartSizeInvalid(UploadSessionError):
    code = "upload_part_size_invalid"
    message = "The part size does not match the upload plan."


class UploadPartChecksumInvalid(UploadSessionError):
    code = "upload_part_checksum_invalid"
    message = "The part checksum must be canonical base64 SHA-256."


class UploadPartChecksumConflict(UploadSessionError):
    code = "upload_part_checksum_conflict"
    message = "The part already has a different checksum expectation."


class UploadCompletionPartsInvalid(UploadSessionError):
    code = "upload_completion_parts_invalid"
    message = "The ordered completion parts do not match the upload plan."


class UploadCompletionVerificationFailed(UploadSessionError):
    code = "upload_completion_verification_failed"
    message = "The completed object did not satisfy the upload verification contract."


class UploadCompletionStateInvalid(UploadSessionError):
    code = "upload_completion_state_invalid"
    message = "The upload completion state is inconsistent."


class UploadCompletionFailed(UploadSessionError):
    code = "upload_completion_failed"
    message = "The upload could not be finalized."


class UploadAbortConflict(UploadSessionError):
    code = "upload_abort_conflict"
    message = "The upload session cannot be aborted in its current state."


class UploadAbortFailed(UploadSessionError):
    code = "upload_abort_failed"
    message = "The upload session abort could not be finalized."


@dataclass(frozen=True, slots=True)
class PresignUploadPartInput:
    size_bytes: int
    checksum_sha256_b64: str


@dataclass(frozen=True, slots=True)
class PresignUploadPartResult:
    part_number: int
    size_bytes: int
    checksum_sha256_b64: str
    url: str
    headers: Mapping[str, str]
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class VerifiedUploadPart:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_b64: str


@dataclass(frozen=True, slots=True)
class GetUploadSessionResult:
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
    uploaded_parts: tuple[VerifiedUploadPart, ...]


@dataclass(frozen=True, slots=True)
class CompleteUploadPartInput:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_b64: str


@dataclass(frozen=True, slots=True)
class CompleteUploadSessionInput:
    parts: tuple[CompleteUploadPartInput, ...]


@dataclass(frozen=True, slots=True)
class CompleteUploadSessionResult:
    session_id: UUID
    status: str
    document_id: UUID
    version_id: UUID
    completed_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class AbortUploadSessionResult:
    session_id: UUID
    status: str
    replayed: bool


class StaleCompletionOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED_MISSING = "failed_missing"
    FAILED_INVALID_OWNED = "failed_invalid_owned"
    FAILED_AMBIGUOUS = "failed_ambiguous"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ReconcileStaleCompletionResult:
    outcome: StaleCompletionOutcome
    completion: CompleteUploadSessionResult | None = None


@dataclass(frozen=True, slots=True)
class _UploadSessionSnapshot:
    session_id: UUID
    tenant_id: UUID
    actor_id: UUID
    status: str
    pending_document_id: UUID
    pending_version_id: UUID
    document_version_id: UUID | None
    object_key: str
    object_store_upload_id: str | None
    filename: str
    extension: str
    media_type: str
    size_bytes: int
    declared_sha256: str
    part_size_bytes: int
    expected_part_count: int
    expires_at: datetime
    completion_started_at: datetime | None
    completed_at: datetime | None
    cleanup_claimed_at: datetime | None
    cleanup_claim_token: UUID | None


@dataclass(frozen=True, slots=True)
class _UploadPartObservation:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_b64: str
    matches_expectation: bool


class UploadSessionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
        object_store: MultipartObjectStore,
        documents_bucket: str,
        settings: UploadSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.object_store = object_store
        self.documents_bucket = documents_bucket
        self.settings = settings if settings is not None else UploadSettings()
        self.clock = clock if clock is not None else _utc_now

    async def presign_part(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
        part_number: int,
        request: PresignUploadPartInput,
    ) -> PresignUploadPartResult:
        checksum = validate_part_checksum_sha256(request.checksum_sha256_b64)
        tenant_id, actor_id = _principal_ids(principal)
        session_factory = self._session_factory()

        async with session_factory.begin() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                ).with_for_update()
            )
            if upload_session is None:
                raise UploadSessionNotFound()
            now = self.clock()
            _require_active(upload_session, now=now)
            expires_in_seconds = int((upload_session.expires_at - now).total_seconds())
            if expires_in_seconds < 1:
                raise UploadSessionExpired()
            expected_size = calculate_expected_part_size(
                size_bytes=upload_session.size_bytes,
                part_size_bytes=upload_session.part_size_bytes,
                expected_part_count=upload_session.expected_part_count,
                part_number=part_number,
            )
            if request.size_bytes != expected_size:
                raise UploadPartSizeInvalid()

            upload_part = await database.scalar(
                select(UploadPart)
                .where(
                    UploadPart.tenant_id == tenant_id,
                    UploadPart.upload_session_id == session_id,
                    UploadPart.part_number == part_number,
                )
                .with_for_update()
            )
            if upload_part is None:
                database.add(
                    UploadPart(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        upload_session_id=session_id,
                        part_number=part_number,
                        expected_checksum_sha256=checksum,
                    )
                )
                await database.flush()
            elif upload_part.expected_checksum_sha256 != checksum:
                raise UploadPartChecksumConflict()

            object_store_upload_id = upload_session.object_store_upload_id
            if object_store_upload_id is None:
                raise UploadSessionNotActive()
            object_key = upload_session.object_key

        signed = await self.object_store.presign_upload_part(
            bucket=self.documents_bucket,
            key=object_key,
            upload_id=object_store_upload_id,
            part_number=part_number,
            checksum_sha256_b64=checksum,
            expires_in_seconds=expires_in_seconds,
        )
        return PresignUploadPartResult(
            part_number=part_number,
            size_bytes=expected_size,
            checksum_sha256_b64=checksum,
            url=signed.url,
            headers=signed.headers,
            expires_in_seconds=signed.expires_in_seconds,
        )

    async def get(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
    ) -> GetUploadSessionResult:
        tenant_id, actor_id = _principal_ids(principal)
        session_factory = self._session_factory()

        async with session_factory() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
            )
            if upload_session is None:
                raise UploadSessionNotFound()
            snapshot = _snapshot(upload_session)
            expected_parts = tuple(
                (
                    await database.scalars(
                        select(UploadPart).where(
                            UploadPart.tenant_id == tenant_id,
                            UploadPart.upload_session_id == session_id,
                        )
                    )
                ).all()
            )
            observation_at = self.clock()
            observation_version = None
            if (
                snapshot.status == UploadSessionStatus.ACTIVE.value
                and snapshot.expires_at > observation_at
            ):
                observation_version = await database.scalar(
                    select(UPLOAD_PART_OBSERVATION_VERSION_SEQUENCE.next_value())
                )

        if (
            snapshot.status
            in {
                UploadSessionStatus.INITIALIZING.value,
                UploadSessionStatus.ACTIVE.value,
            }
            and snapshot.expires_at <= observation_at
        ):
            raise UploadSessionExpired()
        if snapshot.status != UploadSessionStatus.ACTIVE.value:
            return _get_result(snapshot, uploaded_parts=())
        if snapshot.object_store_upload_id is None:
            raise UploadSessionNotActive()
        if observation_version is None:
            raise RuntimeError("upload part observation version is unavailable")

        listed_parts = await self.object_store.list_parts(
            bucket=self.documents_bucket,
            key=snapshot.object_key,
            upload_id=snapshot.object_store_upload_id,
        )
        expected_checksums = {
            part.part_number: part.expected_checksum_sha256 for part in expected_parts
        }
        observations = _part_observations(
            snapshot=snapshot,
            expected_checksums=expected_checksums,
            listed_parts=listed_parts,
        )
        verified_parts = await self._persist_part_observations(
            snapshot=snapshot,
            expected_checksums=expected_checksums,
            observations=observations,
            observation_at=observation_at,
            observation_version=observation_version,
        )
        return _get_result(snapshot, uploaded_parts=verified_parts)

    async def complete(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
        request: CompleteUploadSessionInput,
    ) -> CompleteUploadSessionResult:
        tenant_id, actor_id = _principal_ids(principal)
        snapshot, expected_parts, completed = await self._load_completion_state(
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        if completed is not None:
            return completed
        completion_parts = _validate_completion_request(
            snapshot=snapshot,
            expected_parts=expected_parts,
            requested_parts=request.parts,
        )

        multipart_missing = False
        try:
            listed_parts = await self.object_store.list_parts(
                bucket=self.documents_bucket,
                key=snapshot.object_key,
                upload_id=_required_upload_id(snapshot),
            )
        except MultipartUploadNotFound as error:
            if snapshot.status != UploadSessionStatus.COMPLETING.value:
                refreshed, _, completed = await self._load_completion_state(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
                if completed is not None:
                    return completed
                if refreshed.status != UploadSessionStatus.COMPLETING.value:
                    await self._mark_completion_failed(
                        snapshot=refreshed,
                        expected_status=UploadSessionStatus.ACTIVE.value,
                        error_code=error.code,
                    )
                    raise
                snapshot = refreshed
            multipart_missing = True
            listed_parts = ()
        if not multipart_missing:
            completion_parts = _verify_listed_completion_parts(
                expected=completion_parts,
                listed=listed_parts,
            )

        snapshot, completed = await self._claim_completion(snapshot=snapshot)
        if completed is not None:
            return completed

        completion_result: CompletedMultipartUpload | None = None
        if not multipart_missing:
            try:
                completion_result = await self.object_store.complete_upload(
                    bucket=self.documents_bucket,
                    key=snapshot.object_key,
                    upload_id=_required_upload_id(snapshot),
                    parts=completion_parts,
                )
            except MultipartUploadNotFound:
                completion_result = None

        head = await self.object_store.head_object(
            bucket=self.documents_bucket,
            key=snapshot.object_key,
        )
        identity_verified = _object_identity_matches(snapshot=snapshot, head=head)
        try:
            detected_media_type, transport_checksum = await self._validate_completed_object(
                snapshot=snapshot,
                head=head,
                completion_result=completion_result,
            )
        except (DocumentEnvelopeViolation, UploadCompletionVerificationFailed) as error:
            await self._mark_invalid_completion(
                snapshot=snapshot,
                error_code=error.code,
                delete_object=identity_verified,
            )
            raise

        return await self._finalize_completion(
            snapshot=snapshot,
            head=head,
            detected_media_type=detected_media_type,
            transport_checksum=transport_checksum,
        )

    async def reconcile_stale_completion(
        self,
        *,
        tenant_id: UUID,
        session_id: UUID,
        cleanup_claim_token: UUID,
        stale_before: datetime,
    ) -> ReconcileStaleCompletionResult:
        loaded = await self._load_stale_completion_state(
            tenant_id=tenant_id,
            session_id=session_id,
            cleanup_claim_token=cleanup_claim_token,
            stale_before=stale_before,
        )
        if loaded is None:
            return ReconcileStaleCompletionResult(outcome=StaleCompletionOutcome.SKIPPED)
        snapshot, expected_parts, completed = loaded
        if completed is not None:
            return ReconcileStaleCompletionResult(
                outcome=StaleCompletionOutcome.COMPLETED,
                completion=completed,
            )

        verified_parts = _verified_parts_from_rows(snapshot=snapshot, parts=expected_parts)
        if len(verified_parts) != snapshot.expected_part_count or tuple(
            part.part_number for part in verified_parts
        ) != tuple(range(1, snapshot.expected_part_count + 1)):
            raise UploadCompletionPartsInvalid()
        completion_parts = tuple(
            UploadedPart(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
                etag=part.etag,
                checksum_sha256_b64=part.checksum_sha256_b64,
            )
            for part in verified_parts
        )

        multipart_missing = False
        try:
            listed_parts = await self.object_store.list_parts(
                bucket=self.documents_bucket,
                key=snapshot.object_key,
                upload_id=_required_upload_id(snapshot),
            )
        except MultipartUploadNotFound:
            multipart_missing = True
            listed_parts = ()
        if not multipart_missing:
            completion_parts = _verify_listed_completion_parts(
                expected=completion_parts,
                listed=listed_parts,
            )

        completion_result: CompletedMultipartUpload | None = None
        if not multipart_missing:
            try:
                completion_result = await self.object_store.complete_upload(
                    bucket=self.documents_bucket,
                    key=snapshot.object_key,
                    upload_id=_required_upload_id(snapshot),
                    parts=completion_parts,
                )
            except MultipartUploadNotFound:
                multipart_missing = True

        try:
            head = await self.object_store.head_object(
                bucket=self.documents_bucket,
                key=snapshot.object_key,
            )
        except ObjectStoreNotFound as error:
            if not multipart_missing:
                raise
            marked_failed = await self._mark_completion_failed(
                snapshot=snapshot,
                expected_status=UploadSessionStatus.COMPLETING.value,
                error_code=error.code,
                cleanup_claim_token=cleanup_claim_token,
            )
            return ReconcileStaleCompletionResult(
                outcome=(
                    StaleCompletionOutcome.FAILED_MISSING
                    if marked_failed
                    else StaleCompletionOutcome.SKIPPED
                )
            )

        identity_verified = _object_identity_matches(snapshot=snapshot, head=head)
        try:
            detected_media_type, transport_checksum = await self._validate_completed_object(
                snapshot=snapshot,
                head=head,
                completion_result=completion_result,
            )
        except (DocumentEnvelopeViolation, UploadCompletionVerificationFailed) as error:
            marked_failed = await self._mark_invalid_completion(
                snapshot=snapshot,
                error_code=error.code,
                delete_object=identity_verified,
                cleanup_claim_token=cleanup_claim_token,
                delete_errors_are_fatal=True,
            )
            if not marked_failed:
                outcome = StaleCompletionOutcome.SKIPPED
            elif identity_verified:
                outcome = StaleCompletionOutcome.FAILED_INVALID_OWNED
            else:
                outcome = StaleCompletionOutcome.FAILED_AMBIGUOUS
            return ReconcileStaleCompletionResult(outcome=outcome)

        completed = await self._finalize_completion(
            snapshot=snapshot,
            head=head,
            detected_media_type=detected_media_type,
            transport_checksum=transport_checksum,
            cleanup_claim_token=cleanup_claim_token,
        )
        return ReconcileStaleCompletionResult(
            outcome=StaleCompletionOutcome.COMPLETED,
            completion=completed,
        )

    async def _load_stale_completion_state(
        self,
        *,
        tenant_id: UUID,
        session_id: UUID,
        cleanup_claim_token: UUID,
        stale_before: datetime,
    ) -> (
        tuple[
            _UploadSessionSnapshot,
            tuple[UploadPart, ...],
            CompleteUploadSessionResult | None,
        ]
        | None
    ):
        session_factory = self._session_factory()
        async with session_factory() as database:
            upload_session = await database.scalar(
                select(UploadSession).where(
                    UploadSession.id == session_id,
                    UploadSession.tenant_id == tenant_id,
                )
            )
            if upload_session is None:
                return None
            completed = await _completed_result(
                database,
                upload_session=upload_session,
                replayed=True,
            )
            if completed is not None:
                return _snapshot(upload_session), (), completed
            if (
                upload_session.status != UploadSessionStatus.COMPLETING.value
                or upload_session.completion_started_at is None
                or upload_session.completion_started_at > stale_before
                or upload_session.cleanup_claim_token != cleanup_claim_token
            ):
                return None
            expected_parts = tuple(
                (
                    await database.scalars(
                        select(UploadPart)
                        .where(
                            UploadPart.tenant_id == tenant_id,
                            UploadPart.upload_session_id == session_id,
                        )
                        .order_by(UploadPart.part_number)
                    )
                ).all()
            )
            return _snapshot(upload_session), expected_parts, None

    async def _validate_completed_object(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        head: ObjectHead,
        completion_result: CompletedMultipartUpload | None,
    ) -> tuple[str, str]:
        transport_checksum = _validate_completed_head(
            snapshot=snapshot,
            head=head,
            completion_result=completion_result,
        )
        envelope = await validate_document_envelope(
            object_store=self.object_store,
            bucket=self.documents_bucket,
            key=snapshot.object_key,
            size_bytes=head.size_bytes,
            extension=snapshot.extension,
            settings=self.settings,
        )
        return envelope.detected_media_type, transport_checksum

    async def abort(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
    ) -> AbortUploadSessionResult:
        tenant_id, actor_id = _principal_ids(principal)
        snapshot, replayed = await self._claim_abort(
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        if snapshot.object_store_upload_id is not None:
            try:
                await self.object_store.abort_upload(
                    bucket=self.documents_bucket,
                    key=snapshot.object_key,
                    upload_id=snapshot.object_store_upload_id,
                )
            except MultipartUploadNotFound:
                pass
            await self._clear_aborted_upload_id(snapshot=snapshot)
        return AbortUploadSessionResult(
            session_id=snapshot.session_id,
            status=UploadSessionStatus.ABORTED.value,
            replayed=replayed,
        )

    async def _claim_abort(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> tuple[_UploadSessionSnapshot, bool]:
        session_factory = self._session_factory()
        snapshot: _UploadSessionSnapshot | None = None
        replayed = False
        try:
            async with session_factory.begin() as database:
                tenant = await database.scalar(
                    select(Tenant).where(Tenant.id == tenant_id).with_for_update()
                )
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=session_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                    ).with_for_update()
                )
                if tenant is None or upload_session is None:
                    raise UploadSessionNotFound()
                if (
                    upload_session.status
                    in {
                        UploadSessionStatus.COMPLETING.value,
                        UploadSessionStatus.COMPLETED.value,
                    }
                    or upload_session.document_version_id is not None
                ):
                    raise UploadAbortConflict()
                if upload_session.status == UploadSessionStatus.ABORTED.value:
                    replayed = True
                elif upload_session.status not in {
                    UploadSessionStatus.INITIALIZING.value,
                    UploadSessionStatus.ACTIVE.value,
                }:
                    raise UploadSessionNotActive()
                if tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                    raise UploadCompletionStateInvalid()
                tenant.reserved_storage_bytes -= upload_session.reserved_bytes
                upload_session.reserved_bytes = 0
                upload_session.status = UploadSessionStatus.ABORTED.value
                upload_session.aborted_at = upload_session.aborted_at or self.clock()
                upload_session.last_error_code = None
                upload_session.cleanup_claimed_at = None
                upload_session.cleanup_claim_token = None
                snapshot = _snapshot(upload_session)
        except Exception as error:
            recovered = await self._read_aborted_state(
                session_id=session_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            if recovered is not None:
                return recovered, True
            if isinstance(error, UploadSessionError):
                raise
            raise UploadAbortFailed() from error
        if snapshot is None:
            raise UploadAbortFailed()
        return snapshot, replayed

    async def _read_aborted_state(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> _UploadSessionSnapshot | None:
        session_factory = self._session_factory()
        try:
            async with session_factory() as database:
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=session_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                    )
                )
                if (
                    upload_session is None
                    or upload_session.status != UploadSessionStatus.ABORTED.value
                    or upload_session.reserved_bytes != 0
                    or upload_session.document_version_id is not None
                ):
                    return None
                return _snapshot(upload_session)
        except Exception:
            return None

    async def _clear_aborted_upload_id(self, *, snapshot: _UploadSessionSnapshot) -> None:
        session_factory = self._session_factory()
        try:
            async with session_factory.begin() as database:
                tenant = await database.scalar(
                    select(Tenant).where(Tenant.id == snapshot.tenant_id).with_for_update()
                )
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=snapshot.session_id,
                        tenant_id=snapshot.tenant_id,
                        actor_id=snapshot.actor_id,
                    ).with_for_update()
                )
                if tenant is None or upload_session is None:
                    raise UploadAbortFailed()
                if (
                    upload_session.status != UploadSessionStatus.ABORTED.value
                    or upload_session.document_version_id is not None
                    or not _completion_identity_matches(
                        upload_session=upload_session,
                        snapshot=snapshot,
                    )
                ):
                    raise UploadAbortFailed()
                if upload_session.object_store_upload_id is None:
                    return
                if upload_session.object_store_upload_id != snapshot.object_store_upload_id:
                    raise UploadAbortFailed()
                upload_session.object_store_upload_id = None
                upload_session.last_error_code = None
        except Exception as error:
            recovered = await self._read_aborted_state(
                session_id=snapshot.session_id,
                tenant_id=snapshot.tenant_id,
                actor_id=snapshot.actor_id,
            )
            if recovered is not None and recovered.object_store_upload_id is None:
                return
            if isinstance(error, UploadAbortFailed):
                raise
            raise UploadAbortFailed() from error

    async def _load_completion_state(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> tuple[
        _UploadSessionSnapshot,
        tuple[UploadPart, ...],
        CompleteUploadSessionResult | None,
    ]:
        session_factory = self._session_factory()
        async with session_factory() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
            )
            if upload_session is None:
                raise UploadSessionNotFound()
            completed = await _completed_result(
                database,
                upload_session=upload_session,
                replayed=True,
            )
            if completed is not None:
                return _snapshot(upload_session), (), completed
            if upload_session.status == UploadSessionStatus.ACTIVE.value:
                if upload_session.expires_at <= self.clock():
                    raise UploadSessionExpired()
            elif upload_session.status != UploadSessionStatus.COMPLETING.value:
                raise UploadSessionNotActive()
            expected_parts = tuple(
                (
                    await database.scalars(
                        select(UploadPart)
                        .where(
                            UploadPart.tenant_id == tenant_id,
                            UploadPart.upload_session_id == session_id,
                        )
                        .order_by(UploadPart.part_number)
                    )
                ).all()
            )
            return _snapshot(upload_session), expected_parts, None

    async def _claim_completion(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
    ) -> tuple[_UploadSessionSnapshot, CompleteUploadSessionResult | None]:
        session_factory = self._session_factory()
        claimed_snapshot: _UploadSessionSnapshot | None = None
        completed: CompleteUploadSessionResult | None = None
        async with session_factory.begin() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=snapshot.session_id,
                    tenant_id=snapshot.tenant_id,
                    actor_id=snapshot.actor_id,
                ).with_for_update()
            )
            if upload_session is None:
                raise UploadSessionNotFound()
            completed = await _completed_result(
                database,
                upload_session=upload_session,
                replayed=True,
            )
            if completed is None:
                if upload_session.status == UploadSessionStatus.ACTIVE.value:
                    if upload_session.expires_at <= self.clock():
                        raise UploadSessionExpired()
                    upload_session.status = UploadSessionStatus.COMPLETING.value
                    upload_session.completion_started_at = self.clock()
                elif upload_session.status != UploadSessionStatus.COMPLETING.value:
                    raise UploadSessionNotActive()
                if (
                    upload_session.object_key != snapshot.object_key
                    or upload_session.object_store_upload_id != snapshot.object_store_upload_id
                    or upload_session.pending_document_id != snapshot.pending_document_id
                    or upload_session.pending_version_id != snapshot.pending_version_id
                ):
                    raise UploadCompletionStateInvalid()
                upload_session.cleanup_claimed_at = None
                upload_session.cleanup_claim_token = None
                claimed_snapshot = _snapshot(upload_session)
        if completed is not None:
            return snapshot, completed
        if claimed_snapshot is None:
            raise UploadCompletionStateInvalid()
        return claimed_snapshot, None

    async def _finalize_completion(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        head: ObjectHead,
        detected_media_type: str,
        transport_checksum: str,
        cleanup_claim_token: UUID | None = None,
    ) -> CompleteUploadSessionResult:
        session_factory = self._session_factory()
        result: CompleteUploadSessionResult | None = None
        try:
            async with session_factory.begin() as database:
                tenant = await database.scalar(
                    select(Tenant).where(Tenant.id == snapshot.tenant_id).with_for_update()
                )
                if tenant is None:
                    raise UploadCompletionStateInvalid()
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=snapshot.session_id,
                        tenant_id=snapshot.tenant_id,
                        actor_id=snapshot.actor_id,
                    ).with_for_update()
                )
                if upload_session is None:
                    raise UploadSessionNotFound()
                result = await _completed_result(
                    database,
                    upload_session=upload_session,
                    replayed=True,
                )
                if result is None:
                    if (
                        upload_session.status != UploadSessionStatus.COMPLETING.value
                        or upload_session.object_key != snapshot.object_key
                        or upload_session.pending_document_id != snapshot.pending_document_id
                        or upload_session.pending_version_id != snapshot.pending_version_id
                        or upload_session.reserved_bytes != upload_session.size_bytes
                        or tenant.reserved_storage_bytes < upload_session.reserved_bytes
                        or (
                            cleanup_claim_token is not None
                            and upload_session.cleanup_claim_token != cleanup_claim_token
                        )
                    ):
                        raise UploadCompletionStateInvalid()
                    document = Document(
                        id=upload_session.pending_document_id,
                        tenant_id=upload_session.tenant_id,
                        created_by=upload_session.actor_id,
                        title=upload_session.original_filename,
                    )
                    database.add(document)
                    await database.flush()
                    version = DocumentVersion(
                        id=upload_session.pending_version_id,
                        tenant_id=upload_session.tenant_id,
                        document_id=upload_session.pending_document_id,
                        upload_session_id=upload_session.id,
                        version_number=1,
                        status=DocumentVersionStatus.UPLOADED.value,
                        object_key=upload_session.object_key,
                        original_filename=upload_session.original_filename,
                        declared_media_type=upload_session.declared_media_type,
                        detected_media_type=detected_media_type,
                        size_bytes=head.size_bytes,
                        declared_sha256=upload_session.declared_sha256,
                        content_sha256_verified_at=None,
                        transport_checksum_sha256=transport_checksum,
                        created_by=upload_session.actor_id,
                    )
                    database.add(version)
                    await database.flush()
                    await create_job_records(
                        database,
                        tenant_id=upload_session.tenant_id,
                        actor_id=upload_session.actor_id,
                        job_type="document.ingest",
                        idempotency_key=f"document-version:{version.id}",
                        payload={"document_version_id": str(version.id)},
                        document_version_id=version.id,
                        request_id=None,
                        correlation_id=None,
                        outbox_event_type="document.ingest.requested",
                    )
                    reserved_bytes = upload_session.reserved_bytes
                    tenant.reserved_storage_bytes -= reserved_bytes
                    tenant.used_storage_bytes += head.size_bytes
                    upload_session.reserved_bytes = 0
                    upload_session.document_version_id = version.id
                    upload_session.status = UploadSessionStatus.COMPLETED.value
                    upload_session.completed_at = self.clock()
                    upload_session.last_error_code = None
                    upload_session.cleanup_claimed_at = None
                    upload_session.cleanup_claim_token = None
                    await database.flush()
                    result = _result_from_completed_models(
                        upload_session=upload_session,
                        version=version,
                        replayed=False,
                    )
        except Exception as error:
            recovered = await self._read_completed_result(
                tenant_id=snapshot.tenant_id,
                actor_id=snapshot.actor_id,
                session_id=snapshot.session_id,
                replayed=False,
            )
            if recovered is not None:
                return recovered
            if isinstance(error, UploadSessionError):
                raise
            raise UploadCompletionFailed() from error
        if result is None:
            raise UploadCompletionFailed()
        return result

    async def _read_completed_result(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        replayed: bool,
    ) -> CompleteUploadSessionResult | None:
        session_factory = self._session_factory()
        async with session_factory() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
            )
            if upload_session is None:
                return None
            return await _completed_result(
                database,
                upload_session=upload_session,
                replayed=replayed,
            )

    async def _mark_invalid_completion(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        error_code: str,
        delete_object: bool,
        cleanup_claim_token: UUID | None = None,
        delete_errors_are_fatal: bool = False,
    ) -> bool:
        marked_failed = await self._mark_completion_failed(
            snapshot=snapshot,
            expected_status=UploadSessionStatus.COMPLETING.value,
            error_code=error_code,
            cleanup_claim_token=cleanup_claim_token,
        )
        if not marked_failed or not delete_object:
            return marked_failed
        try:
            await self.object_store.delete_object(
                bucket=self.documents_bucket,
                key=snapshot.object_key,
            )
        except Exception:
            if delete_errors_are_fatal:
                raise
            _LOGGER.warning(
                "invalid completed upload deletion failed",
                extra={
                    "event_data": {"error_code": "upload_completion_invalid_object_delete_failed"}
                },
            )
        return marked_failed

    async def _mark_completion_failed(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        expected_status: str,
        error_code: str,
        cleanup_claim_token: UUID | None = None,
    ) -> bool:
        session_factory = self._session_factory()
        marked_failed = False
        try:
            async with session_factory.begin() as database:
                tenant = await database.scalar(
                    select(Tenant).where(Tenant.id == snapshot.tenant_id).with_for_update()
                )
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=snapshot.session_id,
                        tenant_id=snapshot.tenant_id,
                        actor_id=snapshot.actor_id,
                    ).with_for_update()
                )
                if tenant is None or upload_session is None:
                    return False
                if (
                    upload_session.status != expected_status
                    or upload_session.document_version_id is not None
                    or not _completion_identity_matches(
                        upload_session=upload_session,
                        snapshot=snapshot,
                    )
                    or (
                        cleanup_claim_token is not None
                        and upload_session.cleanup_claim_token != cleanup_claim_token
                    )
                ):
                    return False
                if tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                    raise UploadCompletionStateInvalid()
                tenant.reserved_storage_bytes -= upload_session.reserved_bytes
                upload_session.reserved_bytes = 0
                upload_session.status = UploadSessionStatus.FAILED.value
                upload_session.last_error_code = error_code
                upload_session.cleanup_claimed_at = None
                upload_session.cleanup_claim_token = None
                marked_failed = True
        except Exception:
            marked_failed = await self._completion_failure_persisted(
                snapshot=snapshot,
                error_code=error_code,
                cleanup_claim_token=cleanup_claim_token,
            )
            if not marked_failed:
                _LOGGER.warning(
                    "upload completion failure could not be preserved",
                    extra={
                        "event_data": {
                            "error_code": "upload_completion_failure_preservation_failed"
                        }
                    },
                )
        return marked_failed

    async def _completion_failure_persisted(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        error_code: str,
        cleanup_claim_token: UUID | None = None,
    ) -> bool:
        session_factory = self._session_factory()
        try:
            async with session_factory() as database:
                upload_session = await database.scalar(
                    _owned_session_query(
                        session_id=snapshot.session_id,
                        tenant_id=snapshot.tenant_id,
                        actor_id=snapshot.actor_id,
                    )
                )
                return bool(
                    upload_session is not None
                    and upload_session.status == UploadSessionStatus.FAILED.value
                    and upload_session.reserved_bytes == 0
                    and upload_session.document_version_id is None
                    and upload_session.last_error_code == error_code
                    and (
                        cleanup_claim_token is None
                        or (
                            upload_session.cleanup_claimed_at is None
                            and upload_session.cleanup_claim_token is None
                        )
                    )
                    and _completion_identity_matches(
                        upload_session=upload_session,
                        snapshot=snapshot,
                    )
                )
        except Exception:
            return False

    async def _persist_part_observations(
        self,
        *,
        snapshot: _UploadSessionSnapshot,
        expected_checksums: Mapping[int, str],
        observations: Sequence[_UploadPartObservation],
        observation_at: datetime,
        observation_version: int,
    ) -> tuple[VerifiedUploadPart, ...]:
        session_factory = self._session_factory()
        async with session_factory.begin() as database:
            upload_session = await database.scalar(
                _owned_session_query(
                    session_id=snapshot.session_id,
                    tenant_id=snapshot.tenant_id,
                    actor_id=snapshot.actor_id,
                ).with_for_update()
            )
            if upload_session is None:
                raise UploadSessionNotFound()
            _require_active(upload_session, now=self.clock())
            if (
                upload_session.object_key != snapshot.object_key
                or upload_session.object_store_upload_id != snapshot.object_store_upload_id
            ):
                raise UploadSessionNotActive()

            current_parts = {
                part.part_number: part
                for part in (
                    await database.scalars(
                        select(UploadPart)
                        .where(
                            UploadPart.tenant_id == snapshot.tenant_id,
                            UploadPart.upload_session_id == snapshot.session_id,
                        )
                        .with_for_update()
                    )
                ).all()
            }
            for observed in observations:
                current = current_parts.get(observed.part_number)
                expected_checksum = expected_checksums.get(observed.part_number)
                if (
                    current is None
                    or expected_checksum is None
                    or current.expected_checksum_sha256 != expected_checksum
                ):
                    continue
                if (
                    current.observation_version is not None
                    and current.observation_version >= observation_version
                ):
                    continue
                current.observation_version = observation_version
                current.observed_at = observation_at
                if observed.matches_expectation:
                    current.observed_checksum_sha256 = observed.checksum_sha256_b64
                    current.etag = observed.etag
                    current.size_bytes = observed.size_bytes
                    current.verified_at = observation_at
                else:
                    current.observed_checksum_sha256 = None
                    current.etag = None
                    current.size_bytes = None
                    current.verified_at = None
            return _verified_parts_from_rows(
                snapshot=snapshot,
                parts=tuple(current_parts.values()),
            )

    def _session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("upload session database is unavailable")
        return self.session_factory


def validate_part_checksum_sha256(checksum: str) -> str:
    try:
        decoded = base64.b64decode(checksum, validate=True)
    except ValueError as error:
        raise UploadPartChecksumInvalid() from error
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != checksum:
        raise UploadPartChecksumInvalid()
    return checksum


def calculate_expected_part_size(
    *,
    size_bytes: int,
    part_size_bytes: int,
    expected_part_count: int,
    part_number: int,
) -> int:
    if not 1 <= part_number <= expected_part_count:
        raise UploadPartNumberInvalid()
    if part_number < expected_part_count:
        return part_size_bytes
    final_size = size_bytes - part_size_bytes * (expected_part_count - 1)
    if final_size <= 0:
        raise UploadPartSizeInvalid()
    return final_size


def _validate_completion_request(
    *,
    snapshot: _UploadSessionSnapshot,
    expected_parts: Sequence[UploadPart],
    requested_parts: Sequence[CompleteUploadPartInput],
) -> tuple[UploadedPart, ...]:
    expected_numbers = list(range(1, snapshot.expected_part_count + 1))
    if (
        len(requested_parts) != snapshot.expected_part_count
        or [part.part_number for part in requested_parts] != expected_numbers
        or len(expected_parts) != snapshot.expected_part_count
        or [part.part_number for part in expected_parts] != expected_numbers
    ):
        raise UploadCompletionPartsInvalid()
    completion_parts: list[UploadedPart] = []
    for expected_part, requested_part in zip(expected_parts, requested_parts, strict=True):
        if (
            isinstance(requested_part.part_number, bool)
            or not isinstance(requested_part.part_number, int)
            or isinstance(requested_part.size_bytes, bool)
            or not isinstance(requested_part.size_bytes, int)
            or not isinstance(requested_part.etag, str)
            or not requested_part.etag
            or not isinstance(requested_part.checksum_sha256_b64, str)
        ):
            raise UploadCompletionPartsInvalid()
        try:
            checksum = validate_part_checksum_sha256(requested_part.checksum_sha256_b64)
            expected_size = calculate_expected_part_size(
                size_bytes=snapshot.size_bytes,
                part_size_bytes=snapshot.part_size_bytes,
                expected_part_count=snapshot.expected_part_count,
                part_number=requested_part.part_number,
            )
        except UploadSessionError as error:
            raise UploadCompletionPartsInvalid() from error
        if (
            requested_part.size_bytes != expected_size
            or expected_part.expected_checksum_sha256 != checksum
        ):
            raise UploadCompletionPartsInvalid()
        completion_parts.append(
            UploadedPart(
                part_number=requested_part.part_number,
                size_bytes=requested_part.size_bytes,
                etag=requested_part.etag,
                checksum_sha256_b64=checksum,
            )
        )
    return tuple(completion_parts)


def _verify_listed_completion_parts(
    *,
    expected: Sequence[UploadedPart],
    listed: Sequence[UploadedPart],
) -> tuple[UploadedPart, ...]:
    if len(listed) != len(expected):
        raise UploadCompletionVerificationFailed()
    for expected_part, listed_part in zip(expected, listed, strict=True):
        if (
            listed_part.part_number != expected_part.part_number
            or listed_part.size_bytes != expected_part.size_bytes
            or listed_part.etag != expected_part.etag
            or listed_part.checksum_sha256_b64 != expected_part.checksum_sha256_b64
        ):
            raise UploadCompletionVerificationFailed()
    return tuple(listed)


def _required_upload_id(snapshot: _UploadSessionSnapshot) -> str:
    if snapshot.object_store_upload_id is None:
        raise UploadCompletionStateInvalid()
    return snapshot.object_store_upload_id


def _completion_identity_matches(
    *,
    upload_session: UploadSession,
    snapshot: _UploadSessionSnapshot,
) -> bool:
    return (
        upload_session.object_key == snapshot.object_key
        and upload_session.object_store_upload_id == snapshot.object_store_upload_id
        and upload_session.pending_document_id == snapshot.pending_document_id
        and upload_session.pending_version_id == snapshot.pending_version_id
    )


def _object_identity_matches(
    *,
    snapshot: _UploadSessionSnapshot,
    head: ObjectHead,
) -> bool:
    return all(
        head.metadata.get(name) == value
        for name, value in {
            "contract": "m1",
            "upload-session-id": str(snapshot.session_id),
            "version-id": str(snapshot.pending_version_id),
            "declared-size": str(snapshot.size_bytes),
        }.items()
    )


def _validate_completed_head(
    *,
    snapshot: _UploadSessionSnapshot,
    head: ObjectHead,
    completion_result: CompletedMultipartUpload | None,
) -> str:
    if (
        head.size_bytes != snapshot.size_bytes
        or not _object_identity_matches(snapshot=snapshot, head=head)
        or head.checksum_sha256_b64 is None
        or not head.checksum_sha256_b64
        or len(head.checksum_sha256_b64) > 128
    ):
        raise UploadCompletionVerificationFailed()
    if completion_result is not None and (
        completion_result.etag != head.etag
        or completion_result.checksum_sha256_b64 is None
        or completion_result.checksum_sha256_b64 != head.checksum_sha256_b64
    ):
        raise UploadCompletionVerificationFailed()
    return head.checksum_sha256_b64


async def _completed_result(
    database: AsyncSession,
    *,
    upload_session: UploadSession,
    replayed: bool,
) -> CompleteUploadSessionResult | None:
    if upload_session.document_version_id is None:
        if upload_session.status == UploadSessionStatus.COMPLETED.value:
            raise UploadCompletionStateInvalid()
        return None
    if (
        upload_session.status != UploadSessionStatus.COMPLETED.value
        or upload_session.completed_at is None
    ):
        raise UploadCompletionStateInvalid()
    version = await database.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == upload_session.document_version_id,
            DocumentVersion.tenant_id == upload_session.tenant_id,
            DocumentVersion.upload_session_id == upload_session.id,
        )
    )
    if (
        version is None
        or version.id != upload_session.pending_version_id
        or version.document_id != upload_session.pending_document_id
        or version.status != DocumentVersionStatus.UPLOADED.value
    ):
        raise UploadCompletionStateInvalid()
    return _result_from_completed_models(
        upload_session=upload_session,
        version=version,
        replayed=replayed,
    )


def _result_from_completed_models(
    *,
    upload_session: UploadSession,
    version: DocumentVersion,
    replayed: bool,
) -> CompleteUploadSessionResult:
    if upload_session.completed_at is None:
        raise UploadCompletionStateInvalid()
    return CompleteUploadSessionResult(
        session_id=upload_session.id,
        status=UploadSessionStatus.COMPLETED.value,
        document_id=version.document_id,
        version_id=version.id,
        completed_at=upload_session.completed_at,
        replayed=replayed,
    )


def _principal_ids(principal: PrincipalContext) -> tuple[UUID, UUID]:
    try:
        return UUID(principal.tenant_id), UUID(principal.actor_id)
    except ValueError as error:
        raise UploadSessionNotFound() from error


def _owned_session_query(
    *,
    session_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> Select[tuple[UploadSession]]:
    return select(UploadSession).where(
        UploadSession.id == session_id,
        UploadSession.tenant_id == tenant_id,
        UploadSession.actor_id == actor_id,
    )


def _require_active(upload_session: UploadSession, *, now: datetime) -> None:
    if upload_session.expires_at <= now:
        raise UploadSessionExpired()
    if upload_session.status != UploadSessionStatus.ACTIVE.value:
        raise UploadSessionNotActive()


def _snapshot(upload_session: UploadSession) -> _UploadSessionSnapshot:
    return _UploadSessionSnapshot(
        session_id=upload_session.id,
        tenant_id=upload_session.tenant_id,
        actor_id=upload_session.actor_id,
        status=upload_session.status,
        pending_document_id=upload_session.pending_document_id,
        pending_version_id=upload_session.pending_version_id,
        document_version_id=upload_session.document_version_id,
        object_key=upload_session.object_key,
        object_store_upload_id=upload_session.object_store_upload_id,
        filename=upload_session.original_filename,
        extension=upload_session.extension,
        media_type=upload_session.declared_media_type,
        size_bytes=upload_session.size_bytes,
        declared_sha256=upload_session.declared_sha256,
        part_size_bytes=upload_session.part_size_bytes,
        expected_part_count=upload_session.expected_part_count,
        expires_at=upload_session.expires_at,
        completion_started_at=upload_session.completion_started_at,
        completed_at=upload_session.completed_at,
        cleanup_claimed_at=upload_session.cleanup_claimed_at,
        cleanup_claim_token=upload_session.cleanup_claim_token,
    )


def _part_observations(
    *,
    snapshot: _UploadSessionSnapshot,
    expected_checksums: Mapping[int, str],
    listed_parts: Sequence[UploadedPart],
) -> tuple[_UploadPartObservation, ...]:
    observations: list[_UploadPartObservation] = []
    seen_part_numbers: set[int] = set()
    for listed in listed_parts:
        if listed.part_number in seen_part_numbers:
            continue
        seen_part_numbers.add(listed.part_number)
        expected_checksum = expected_checksums.get(listed.part_number)
        if expected_checksum is None:
            continue
        try:
            expected_size = calculate_expected_part_size(
                size_bytes=snapshot.size_bytes,
                part_size_bytes=snapshot.part_size_bytes,
                expected_part_count=snapshot.expected_part_count,
                part_number=listed.part_number,
            )
        except UploadSessionError:
            continue
        observations.append(
            _UploadPartObservation(
                part_number=listed.part_number,
                size_bytes=listed.size_bytes,
                etag=listed.etag,
                checksum_sha256_b64=listed.checksum_sha256_b64,
                matches_expectation=(
                    listed.checksum_sha256_b64 == expected_checksum
                    and listed.size_bytes == expected_size
                ),
            )
        )
    return tuple(sorted(observations, key=lambda part: part.part_number))


def _verified_parts_from_rows(
    *,
    snapshot: _UploadSessionSnapshot,
    parts: Sequence[UploadPart],
) -> tuple[VerifiedUploadPart, ...]:
    verified: list[VerifiedUploadPart] = []
    for part in parts:
        if (
            part.verified_at is None
            or part.observed_checksum_sha256 is None
            or part.etag is None
            or part.size_bytes is None
            or part.observed_checksum_sha256 != part.expected_checksum_sha256
        ):
            continue
        try:
            expected_size = calculate_expected_part_size(
                size_bytes=snapshot.size_bytes,
                part_size_bytes=snapshot.part_size_bytes,
                expected_part_count=snapshot.expected_part_count,
                part_number=part.part_number,
            )
        except UploadSessionError:
            continue
        if part.size_bytes != expected_size:
            continue
        verified.append(
            VerifiedUploadPart(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
                etag=part.etag,
                checksum_sha256_b64=part.observed_checksum_sha256,
            )
        )
    return tuple(sorted(verified, key=lambda part: part.part_number))


def _get_result(
    snapshot: _UploadSessionSnapshot,
    *,
    uploaded_parts: tuple[VerifiedUploadPart, ...],
) -> GetUploadSessionResult:
    return GetUploadSessionResult(
        session_id=snapshot.session_id,
        status=snapshot.status,
        filename=snapshot.filename,
        extension=snapshot.extension,
        media_type=snapshot.media_type,
        size_bytes=snapshot.size_bytes,
        declared_sha256=snapshot.declared_sha256,
        part_size_bytes=snapshot.part_size_bytes,
        expected_part_count=snapshot.expected_part_count,
        expires_at=snapshot.expires_at,
        uploaded_parts=uploaded_parts,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
