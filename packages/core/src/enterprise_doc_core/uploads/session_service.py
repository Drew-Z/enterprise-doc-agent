from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store import MultipartObjectStore, UploadedPart
from enterprise_doc_core.uploads.models import (
    UPLOAD_PART_OBSERVATION_VERSION_SEQUENCE,
    UploadPart,
    UploadSession,
    UploadSessionStatus,
)


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
class _UploadSessionSnapshot:
    session_id: UUID
    tenant_id: UUID
    actor_id: UUID
    status: str
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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.object_store = object_store
        self.documents_bucket = documents_bucket
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
