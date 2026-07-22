from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import ObjectStoreChecksumMode, UploadSettings
from enterprise_doc_core.documents import DocumentVersion
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.object_store import (
    MultipartObjectStore,
    MultipartUploadNotFound,
    ObjectHead,
    ObjectStoreNotFound,
)
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus
from enterprise_doc_core.uploads.policy import M1_UPLOAD_PREFIX, parse_upload_object_key
from enterprise_doc_core.uploads.session_service import (
    StaleCompletionOutcome,
    UploadSessionService,
)

_COUNTER_NAMES = (
    "sessionCandidates",
    "sessionsClaimed",
    "expiryCandidates",
    "staleCompletionCandidates",
    "terminalUploadCandidates",
    "reservationRepairCandidates",
    "sessionsExpired",
    "staleCompleted",
    "staleFailedMissing",
    "staleFailedInvalidOwned",
    "staleFailedAmbiguous",
    "reservationsReleased",
    "multipartAborted",
    "multipartMissing",
    "completedObjectsDeleted",
    "sessionCleanupSucceeded",
    "sessionsSkipped",
    "orphanUploadsScanned",
    "orphanCandidates",
    "orphanAborted",
    "orphanSkippedOwned",
    "orphanSkippedMalformed",
    "orphanSkippedTimestamp",
    "ownershipAmbiguous",
    "processingErrors",
)


class UploadCleanupOwnershipAmbiguous(RuntimeError):
    """The cleanup worker cannot prove destructive ownership."""


class _ClaimKind(StrEnum):
    EXPIRY = "expiry"
    STALE_COMPLETION = "stale_completion"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class _CleanupClaim:
    session_id: UUID
    tenant_id: UUID
    actor_id: UUID
    source_status: str
    pending_document_id: UUID
    pending_version_id: UUID
    document_version_id: UUID | None
    object_key: str
    object_store_upload_id: str | None
    size_bytes: int
    reserved_bytes: int
    expires_at: datetime
    completion_started_at: datetime | None
    claim_token: UUID | None

    @property
    def kind(self) -> _ClaimKind:
        if self.source_status in {
            UploadSessionStatus.INITIALIZING.value,
            UploadSessionStatus.ACTIVE.value,
        }:
            return _ClaimKind.EXPIRY
        if self.source_status == UploadSessionStatus.COMPLETING.value:
            return _ClaimKind.STALE_COMPLETION
        return _ClaimKind.TERMINAL


@dataclass(frozen=True, slots=True)
class UploadCleanupReport:
    dry_run: bool
    counters: Mapping[str, int]
    exceptions_by_class: Mapping[str, int]

    @property
    def failed(self) -> bool:
        return bool(self.exceptions_by_class)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "failed" if self.failed else "ok",
            "dryRun": self.dry_run,
            "counters": dict(self.counters),
            "exceptionsByClass": dict(self.exceptions_by_class),
        }

    def with_exception(self, error: BaseException) -> UploadCleanupReport:
        counters = dict(self.counters)
        counters["processingErrors"] = counters.get("processingErrors", 0) + 1
        exceptions = dict(self.exceptions_by_class)
        name = type(error).__name__
        exceptions[name] = exceptions.get(name, 0) + 1
        return UploadCleanupReport(
            dry_run=self.dry_run,
            counters=counters,
            exceptions_by_class=exceptions,
        )

    @classmethod
    def empty(cls, *, dry_run: bool) -> UploadCleanupReport:
        return cls(
            dry_run=dry_run,
            counters={name: 0 for name in _COUNTER_NAMES},
            exceptions_by_class={},
        )


class _ReportBuilder:
    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.counters = Counter({name: 0 for name in _COUNTER_NAMES})
        self.exceptions_by_class: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def record_error(self, error: BaseException) -> None:
        self.increment("processingErrors")
        self.exceptions_by_class[type(error).__name__] += 1

    def record_ambiguous(self) -> None:
        self.increment("ownershipAmbiguous")
        self.exceptions_by_class[UploadCleanupOwnershipAmbiguous.__name__] += 1

    def build(self) -> UploadCleanupReport:
        return UploadCleanupReport(
            dry_run=self.dry_run,
            counters={name: self.counters[name] for name in _COUNTER_NAMES},
            exceptions_by_class=dict(sorted(self.exceptions_by_class.items())),
        )


class UploadCleanupService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: MultipartObjectStore,
        documents_bucket: str,
        settings: UploadSettings | None = None,
        clock: Callable[[], datetime] | None = None,
        checksum_mode: ObjectStoreChecksumMode = ObjectStoreChecksumMode.NATIVE_SHA256,
    ) -> None:
        self.session_factory = session_factory
        self.object_store = object_store
        self.documents_bucket = documents_bucket
        self.settings = settings if settings is not None else UploadSettings()
        self.clock = clock if clock is not None else _utc_now
        self.session_service = UploadSessionService(
            session_factory=session_factory,
            object_store=object_store,
            documents_bucket=documents_bucket,
            settings=self.settings,
            clock=self.clock,
            checksum_mode=checksum_mode,
        )

    async def run(self, *, dry_run: bool = False) -> UploadCleanupReport:
        report = _ReportBuilder(dry_run=dry_run)
        now = self.clock()
        if now.utcoffset() is None:
            report.record_error(ValueError("cleanup clock must be timezone-aware"))
            return report.build()
        now = now.astimezone(UTC)

        try:
            claims = await self._discover_or_claim_sessions(now=now, dry_run=dry_run)
        except Exception as error:
            report.record_error(error)
            claims = ()

        for claim in claims:
            self._record_candidate(report=report, claim=claim)

        if not dry_run:
            for claim in claims:
                try:
                    await self._process_claim(report=report, claim=claim, now=now)
                except Exception as error:
                    report.record_error(error)

        try:
            await self._scan_orphan_uploads(report=report, now=now, dry_run=dry_run)
        except Exception as error:
            report.record_error(error)
        return report.build()

    async def _discover_or_claim_sessions(
        self,
        *,
        now: datetime,
        dry_run: bool,
    ) -> tuple[_CleanupClaim, ...]:
        expiry_cutoff = now - timedelta(seconds=self.settings.cleanup_expiry_grace_seconds)
        completion_cutoff = now - timedelta(seconds=self.settings.cleanup_completing_grace_seconds)
        claim_cutoff = now - timedelta(seconds=self.settings.cleanup_claim_ttl_seconds)
        claim_available = or_(
            UploadSession.cleanup_claim_token.is_(None),
            UploadSession.cleanup_claimed_at <= claim_cutoff,
        )
        eligible = or_(
            and_(
                UploadSession.status.in_(
                    (
                        UploadSessionStatus.INITIALIZING.value,
                        UploadSessionStatus.ACTIVE.value,
                    )
                ),
                UploadSession.expires_at <= expiry_cutoff,
            ),
            and_(
                UploadSession.status == UploadSessionStatus.COMPLETING.value,
                UploadSession.completion_started_at.is_not(None),
                UploadSession.completion_started_at <= completion_cutoff,
            ),
            and_(
                UploadSession.status == UploadSessionStatus.FAILED.value,
                UploadSession.reserved_bytes > 0,
                UploadSession.document_version_id.is_(None),
            ),
            and_(
                UploadSession.status.in_(
                    (
                        UploadSessionStatus.ABORTED.value,
                        UploadSessionStatus.EXPIRED.value,
                        UploadSessionStatus.FAILED.value,
                    )
                ),
                UploadSession.object_store_upload_id.is_not(None),
                UploadSession.document_version_id.is_(None),
            ),
        )
        query = (
            select(UploadSession)
            .where(claim_available, eligible)
            .order_by(UploadSession.updated_at, UploadSession.id)
            .limit(self.settings.cleanup_batch_size)
        )
        if dry_run:
            async with self.session_factory() as database:
                rows = tuple((await database.scalars(query)).all())
                return tuple(_claim_from_model(row, claim_token=None) for row in rows)

        query = query.with_for_update(skip_locked=True)
        async with self.session_factory.begin() as database:
            rows = tuple((await database.scalars(query)).all())
            claims: list[_CleanupClaim] = []
            for row in rows:
                token = uuid4()
                row.cleanup_claimed_at = now
                row.cleanup_claim_token = token
                claims.append(_claim_from_model(row, claim_token=token))
            await database.flush()
            return tuple(claims)

    def _record_candidate(self, *, report: _ReportBuilder, claim: _CleanupClaim) -> None:
        report.increment("sessionCandidates")
        if not report.dry_run:
            report.increment("sessionsClaimed")
        if claim.kind is _ClaimKind.EXPIRY:
            report.increment("expiryCandidates")
        elif claim.kind is _ClaimKind.STALE_COMPLETION:
            report.increment("staleCompletionCandidates")
        if claim.source_status == UploadSessionStatus.FAILED.value and claim.reserved_bytes > 0:
            report.increment("reservationRepairCandidates")
        if (
            claim.source_status
            in {
                UploadSessionStatus.ABORTED.value,
                UploadSessionStatus.EXPIRED.value,
                UploadSessionStatus.FAILED.value,
            }
            and claim.object_store_upload_id is not None
        ):
            report.increment("terminalUploadCandidates")

    async def _process_claim(
        self,
        *,
        report: _ReportBuilder,
        claim: _CleanupClaim,
        now: datetime,
    ) -> None:
        if claim.claim_token is None:
            raise RuntimeError("cleanup claim token is missing")
        if claim.kind is _ClaimKind.STALE_COMPLETION:
            await self._reconcile_stale_completion(report=report, claim=claim, now=now)
            return
        if claim.kind is _ClaimKind.EXPIRY:
            target, released = await self._expire_session(claim=claim, now=now)
            if target is None:
                report.increment("sessionsSkipped")
                return
            report.increment("sessionsExpired")
            if released:
                report.increment("reservationsReleased")
        else:
            target, released = await self._prepare_terminal_session(claim=claim)
            if target is None:
                report.increment("sessionsSkipped")
                return
            if released:
                report.increment("reservationsReleased")

        if target.object_store_upload_id is None:
            report.increment("sessionCleanupSucceeded")
            return
        await self._cleanup_terminal_upload(report=report, claim=target)

    async def _reconcile_stale_completion(
        self,
        *,
        report: _ReportBuilder,
        claim: _CleanupClaim,
        now: datetime,
    ) -> None:
        if claim.claim_token is None:
            raise RuntimeError("cleanup claim token is missing")
        result = await self.session_service.reconcile_stale_completion(
            tenant_id=claim.tenant_id,
            session_id=claim.session_id,
            cleanup_claim_token=claim.claim_token,
            stale_before=now - timedelta(seconds=self.settings.cleanup_completing_grace_seconds),
        )
        if result.outcome is StaleCompletionOutcome.COMPLETED:
            report.increment("staleCompleted")
            report.increment("sessionCleanupSucceeded")
        elif result.outcome is StaleCompletionOutcome.FAILED_MISSING:
            report.increment("staleFailedMissing")
            report.increment("reservationsReleased")
            report.increment("sessionCleanupSucceeded")
        elif result.outcome is StaleCompletionOutcome.FAILED_INVALID_OWNED:
            report.increment("staleFailedInvalidOwned")
            report.increment("reservationsReleased")
            report.increment("completedObjectsDeleted")
            report.increment("sessionCleanupSucceeded")
        elif result.outcome is StaleCompletionOutcome.FAILED_AMBIGUOUS:
            report.increment("staleFailedAmbiguous")
            report.increment("reservationsReleased")
            report.record_ambiguous()
        else:
            report.increment("sessionsSkipped")

    async def _expire_session(
        self,
        *,
        claim: _CleanupClaim,
        now: datetime,
    ) -> tuple[_CleanupClaim | None, bool]:
        if claim.claim_token is None:
            return None, False
        expiry_cutoff = now - timedelta(seconds=self.settings.cleanup_expiry_grace_seconds)
        async with self.session_factory.begin() as database:
            tenant = await database.scalar(
                select(Tenant).where(Tenant.id == claim.tenant_id).with_for_update()
            )
            upload_session = await database.scalar(
                _claimed_session_query(claim=claim).with_for_update()
            )
            if tenant is None or upload_session is None:
                return None, False
            if (
                not _session_matches_claim(upload_session=upload_session, claim=claim)
                or upload_session.status
                not in {
                    UploadSessionStatus.INITIALIZING.value,
                    UploadSessionStatus.ACTIVE.value,
                }
                or upload_session.expires_at > expiry_cutoff
                or upload_session.document_version_id is not None
            ):
                return None, False
            if tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                raise RuntimeError("upload cleanup quota state is inconsistent")
            released = upload_session.reserved_bytes > 0
            tenant.reserved_storage_bytes -= upload_session.reserved_bytes
            upload_session.reserved_bytes = 0
            upload_session.status = UploadSessionStatus.EXPIRED.value
            upload_session.last_error_code = "upload_session_expired"
            if upload_session.object_store_upload_id is None:
                upload_session.cleanup_claimed_at = None
                upload_session.cleanup_claim_token = None
            await database.flush()
            return (
                replace(
                    _claim_from_model(upload_session, claim_token=claim.claim_token),
                    source_status=UploadSessionStatus.EXPIRED.value,
                ),
                released,
            )

    async def _prepare_terminal_session(
        self,
        *,
        claim: _CleanupClaim,
    ) -> tuple[_CleanupClaim | None, bool]:
        if claim.claim_token is None:
            return None, False
        async with self.session_factory.begin() as database:
            tenant = await database.scalar(
                select(Tenant).where(Tenant.id == claim.tenant_id).with_for_update()
            )
            upload_session = await database.scalar(
                _claimed_session_query(claim=claim).with_for_update()
            )
            if tenant is None or upload_session is None:
                return None, False
            if (
                not _session_matches_claim(upload_session=upload_session, claim=claim)
                or upload_session.status
                not in {
                    UploadSessionStatus.ABORTED.value,
                    UploadSessionStatus.EXPIRED.value,
                    UploadSessionStatus.FAILED.value,
                }
                or upload_session.document_version_id is not None
            ):
                return None, False
            released = False
            if (
                upload_session.status == UploadSessionStatus.FAILED.value
                and upload_session.reserved_bytes > 0
            ):
                if tenant.reserved_storage_bytes < upload_session.reserved_bytes:
                    raise RuntimeError("upload cleanup quota state is inconsistent")
                tenant.reserved_storage_bytes -= upload_session.reserved_bytes
                upload_session.reserved_bytes = 0
                released = True
            if upload_session.object_store_upload_id is None:
                upload_session.cleanup_claimed_at = None
                upload_session.cleanup_claim_token = None
            await database.flush()
            return _claim_from_model(upload_session, claim_token=claim.claim_token), released

    async def _cleanup_terminal_upload(
        self,
        *,
        report: _ReportBuilder,
        claim: _CleanupClaim,
    ) -> None:
        upload_id = claim.object_store_upload_id
        if upload_id is None:
            return
        try:
            await self.object_store.abort_upload(
                bucket=self.documents_bucket,
                key=claim.object_key,
                upload_id=upload_id,
            )
        except MultipartUploadNotFound:
            report.increment("multipartMissing")
            if claim.source_status == UploadSessionStatus.FAILED.value:
                if not await self._cleanup_failed_completed_object(report=report, claim=claim):
                    return
        else:
            report.increment("multipartAborted")

        if await self._finish_terminal_cleanup(claim=claim):
            report.increment("sessionCleanupSucceeded")
        else:
            report.increment("sessionsSkipped")

    async def _cleanup_failed_completed_object(
        self,
        *,
        report: _ReportBuilder,
        claim: _CleanupClaim,
    ) -> bool:
        try:
            head = await self.object_store.head_object(
                bucket=self.documents_bucket,
                key=claim.object_key,
            )
        except ObjectStoreNotFound:
            return True
        if not _object_identity_matches(claim=claim, head=head):
            report.record_ambiguous()
            return False
        if not await self._failed_object_delete_is_safe(claim=claim):
            report.record_ambiguous()
            return False
        try:
            await self.object_store.delete_object(
                bucket=self.documents_bucket,
                key=claim.object_key,
            )
        except ObjectStoreNotFound:
            pass
        report.increment("completedObjectsDeleted")
        return True

    async def _failed_object_delete_is_safe(self, *, claim: _CleanupClaim) -> bool:
        async with self.session_factory.begin() as database:
            tenant = await database.scalar(
                select(Tenant).where(Tenant.id == claim.tenant_id).with_for_update()
            )
            upload_session = await database.scalar(
                _claimed_session_query(claim=claim).with_for_update()
            )
            if (
                tenant is None
                or upload_session is None
                or not _session_matches_claim(upload_session=upload_session, claim=claim)
                or upload_session.status != UploadSessionStatus.FAILED.value
                or upload_session.document_version_id is not None
            ):
                return False
            version_id = await database.scalar(
                select(DocumentVersion.id)
                .where(DocumentVersion.object_key == claim.object_key)
                .limit(1)
            )
            return version_id is None

    async def _finish_terminal_cleanup(self, *, claim: _CleanupClaim) -> bool:
        async with self.session_factory.begin() as database:
            tenant = await database.scalar(
                select(Tenant).where(Tenant.id == claim.tenant_id).with_for_update()
            )
            upload_session = await database.scalar(
                _claimed_session_query(claim=claim).with_for_update()
            )
            if (
                tenant is None
                or upload_session is None
                or not _session_matches_claim(upload_session=upload_session, claim=claim)
                or upload_session.status != claim.source_status
                or upload_session.document_version_id is not None
            ):
                return False
            upload_session.object_store_upload_id = None
            upload_session.cleanup_claimed_at = None
            upload_session.cleanup_claim_token = None
            return True

    async def _scan_orphan_uploads(
        self,
        *,
        report: _ReportBuilder,
        now: datetime,
        dry_run: bool,
    ) -> None:
        uploads = await self.object_store.list_incomplete_uploads(
            bucket=self.documents_bucket,
            prefix=M1_UPLOAD_PREFIX,
        )
        cutoff = now - timedelta(seconds=self.settings.cleanup_orphan_grace_seconds)
        seen: set[tuple[str, str]] = set()
        evaluated = 0
        for upload in uploads:
            report.increment("orphanUploadsScanned")
            identity = (upload.key, upload.upload_id)
            if identity in seen:
                continue
            seen.add(identity)
            parsed = parse_upload_object_key(upload.key)
            if parsed is None:
                report.increment("orphanSkippedMalformed")
                continue
            initiated_at = upload.initiated_at
            if (
                initiated_at is None
                or initiated_at.utcoffset() is None
                or initiated_at.astimezone(UTC) >= cutoff
            ):
                report.increment("orphanSkippedTimestamp")
                continue
            if evaluated >= self.settings.cleanup_batch_size:
                break
            evaluated += 1
            report.increment("orphanCandidates")
            try:
                owned = await self._orphan_is_database_owned(
                    session_id=parsed.session_id,
                    object_key=upload.key,
                )
            except Exception as error:
                report.record_error(error)
                continue
            if owned:
                report.increment("orphanSkippedOwned")
                continue
            if dry_run:
                continue
            try:
                await self.object_store.abort_upload(
                    bucket=self.documents_bucket,
                    key=upload.key,
                    upload_id=upload.upload_id,
                )
            except MultipartUploadNotFound:
                report.increment("multipartMissing")
            except Exception as error:
                report.record_error(error)
            else:
                report.increment("multipartAborted")
                report.increment("orphanAborted")

    async def _orphan_is_database_owned(self, *, session_id: UUID, object_key: str) -> bool:
        async with self.session_factory() as database:
            upload_session_id = await database.scalar(
                select(UploadSession.id)
                .where(
                    or_(
                        UploadSession.id == session_id,
                        UploadSession.object_key == object_key,
                    )
                )
                .limit(1)
            )
            if upload_session_id is not None:
                return True
            version_id = await database.scalar(
                select(DocumentVersion.id).where(DocumentVersion.object_key == object_key).limit(1)
            )
            return version_id is not None


def _claimed_session_query(*, claim: _CleanupClaim) -> Select[tuple[UploadSession]]:
    return select(UploadSession).where(
        UploadSession.id == claim.session_id,
        UploadSession.tenant_id == claim.tenant_id,
        UploadSession.actor_id == claim.actor_id,
    )


def _session_matches_claim(
    *,
    upload_session: UploadSession,
    claim: _CleanupClaim,
) -> bool:
    return (
        upload_session.cleanup_claim_token == claim.claim_token
        and upload_session.pending_document_id == claim.pending_document_id
        and upload_session.pending_version_id == claim.pending_version_id
        and upload_session.document_version_id == claim.document_version_id
        and upload_session.object_key == claim.object_key
        and upload_session.object_store_upload_id == claim.object_store_upload_id
        and upload_session.size_bytes == claim.size_bytes
    )


def _object_identity_matches(*, claim: _CleanupClaim, head: ObjectHead) -> bool:
    return all(
        head.metadata.get(name) == value
        for name, value in {
            "contract": "m1",
            "upload-session-id": str(claim.session_id),
            "version-id": str(claim.pending_version_id),
            "declared-size": str(claim.size_bytes),
        }.items()
    )


def _claim_from_model(
    upload_session: UploadSession,
    *,
    claim_token: UUID | None,
) -> _CleanupClaim:
    return _CleanupClaim(
        session_id=upload_session.id,
        tenant_id=upload_session.tenant_id,
        actor_id=upload_session.actor_id,
        source_status=upload_session.status,
        pending_document_id=upload_session.pending_document_id,
        pending_version_id=upload_session.pending_version_id,
        document_version_id=upload_session.document_version_id,
        object_key=upload_session.object_key,
        object_store_upload_id=upload_session.object_store_upload_id,
        size_bytes=upload_session.size_bytes,
        reserved_bytes=upload_session.reserved_bytes,
        expires_at=upload_session.expires_at,
        completion_started_at=upload_session.completion_started_at,
        claim_token=claim_token,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
