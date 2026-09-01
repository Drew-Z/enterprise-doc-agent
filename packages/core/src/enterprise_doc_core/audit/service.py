from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from enterprise_doc_core.audit.models import (
    AuditArchiveBatch,
    AuditEvent,
    AuditLegalHold,
    AuditRetentionPolicy,
)
from enterprise_doc_core.identity.models import MembershipRole
from enterprise_doc_core.object_store import ArtifactObjectStore
from enterprise_doc_core.object_store.errors import (
    ObjectStoreChecksumMismatch,
    ObjectStoreError,
    ObjectStoreProtocolError,
)


async def append_audit_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    metadata: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    """Append one governance event in the caller's transaction.

    Callers intentionally supply a small, non-sensitive metadata projection. The
    audit table has no update/delete service path and is queried only by tenant.
    """
    event = AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        correlation_id=correlation_id,
        event_metadata=dict(metadata or {}),
    )
    if occurred_at is not None:
        event.occurred_at = occurred_at
    session.add(event)
    await session.flush()
    return event


@dataclass(frozen=True, slots=True)
class AuditEventResult:
    event_id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID | None
    occurred_at: datetime
    request_id: str | None
    correlation_id: str | None
    metadata: dict[str, object]
    schema_version: int


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    items: tuple[AuditEventResult, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AuditRetentionPolicyResult:
    tenant_id: UUID
    retention_days: int
    is_enabled: bool
    updated_by: UUID | None


@dataclass(frozen=True, slots=True)
class AuditLegalHoldResult:
    hold_id: UUID
    tenant_id: UUID
    name: str
    reason: str
    resource_type: str | None
    resource_id: UUID | None
    starts_at: datetime
    expires_at: datetime | None
    released_at: datetime | None
    created_by: UUID | None
    released_by: UUID | None


@dataclass(frozen=True, slots=True)
class AuditRetentionPreview:
    cutoff_at: datetime | None
    eligible_event_count: int
    protected_event_count: int


@dataclass(frozen=True, slots=True)
class AuditRetentionPlan:
    policy: AuditRetentionPolicyResult
    cutoff_at: datetime | None
    eligible_event_count: int
    protected_event_count: int
    eligible_event_ids: tuple[UUID, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AuditArchiveBatchResult:
    batch_id: UUID
    tenant_id: UUID
    cutoff_at: datetime
    archived_event_count: int
    fingerprint: str
    bucket: str
    object_key: str
    content_sha256: str
    size_bytes: int
    created_by: UUID | None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditArchiveVerificationResult:
    batch_id: UUID
    tenant_id: UUID
    verified_at: datetime
    valid: bool
    expected_sha256: str
    actual_sha256: str | None
    expected_size_bytes: int
    actual_size_bytes: int | None
    envelope_valid: bool
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class AuditArchiveDownloadResult:
    batch_id: UUID
    tenant_id: UUID
    bucket: str
    object_key: str
    content_sha256: str
    size_bytes: int
    url: str
    expires_in_seconds: int


class AuditGovernanceError(Exception):
    code = "audit_governance_error"


class AuditGovernanceNotFound(AuditGovernanceError):
    code = "audit_governance_not_found"


class AuditGovernanceForbidden(AuditGovernanceError):
    code = "audit_governance_forbidden"


class AuditGovernanceInvalid(AuditGovernanceError):
    code = "audit_governance_invalid"


class AuditArchiveUnavailable(AuditGovernanceError):
    code = "audit_archive_unavailable"


class AuditArchiveVerificationFailed(AuditGovernanceError):
    code = "audit_archive_verification_failed"


def encode_audit_cursor(*, occurred_at: datetime, event_id: UUID) -> str:
    value = f"{occurred_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_audit_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        occurred_at, event_id = decoded.split("|", 1)
        return datetime.fromisoformat(occurred_at), UUID(event_id)
    except (ValueError, UnicodeDecodeError, UnicodeError) as error:
        raise ValueError("invalid audit cursor") from error


class AuditEventService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_events(
        self,
        *,
        tenant_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> AuditEventPage:
        if not 1 <= limit <= 200:
            raise ValueError("audit event limit must be between 1 and 200")

        statement: Select[tuple[AuditEvent]] = select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id
        )
        if from_date is not None:
            statement = statement.where(AuditEvent.occurred_at >= from_date)
        if to_date is not None:
            statement = statement.where(AuditEvent.occurred_at <= to_date)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if resource_type is not None:
            statement = statement.where(AuditEvent.resource_type == resource_type)
        if resource_id is not None:
            statement = statement.where(AuditEvent.resource_id == resource_id)
        if actor_id is not None:
            statement = statement.where(AuditEvent.actor_id == actor_id)
        if cursor is not None:
            cursor_date, cursor_id = decode_audit_cursor(cursor)
            statement = statement.where(
                (AuditEvent.occurred_at < cursor_date)
                | ((AuditEvent.occurred_at == cursor_date) & (AuditEvent.id < cursor_id))
            )
        statement = statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(
            limit + 1
        )

        async with self.session_factory() as session:
            events = list((await session.scalars(statement)).all())

        has_more = len(events) > limit
        if has_more:
            events = events[:limit]
        results = tuple(
            AuditEventResult(
                event_id=event.id,
                tenant_id=event.tenant_id,
                actor_id=event.actor_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                occurred_at=event.occurred_at,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
                metadata=dict(event.event_metadata),
                schema_version=event.schema_version,
            )
            for event in events
        )
        next_cursor = (
            encode_audit_cursor(occurred_at=results[-1].occurred_at, event_id=results[-1].event_id)
            if has_more and results
            else None
        )
        return AuditEventPage(items=results, next_cursor=next_cursor)


class AuditGovernanceService:
    """Manage tenant retention controls without performing destructive deletion."""

    max_archive_bytes = 16 * 1024 * 1024

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        archive_store: ArtifactObjectStore | None = None,
        archive_bucket: str = "artifacts",
    ) -> None:
        self.session_factory = session_factory
        self.archive_store = archive_store
        self.archive_bucket = archive_bucket

    async def get_retention_policy(self, *, tenant_id: UUID) -> AuditRetentionPolicyResult:
        async with self.session_factory() as session:
            policy = await session.scalar(
                select(AuditRetentionPolicy).where(AuditRetentionPolicy.tenant_id == tenant_id)
            )
        if policy is None:
            return AuditRetentionPolicyResult(
                tenant_id=tenant_id,
                retention_days=365,
                is_enabled=False,
                updated_by=None,
            )
        return _retention_result(policy)

    async def set_retention_policy(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        retention_days: int,
        is_enabled: bool,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditRetentionPolicyResult:
        _require_owner(role)
        if not 30 <= retention_days <= 3650:
            raise AuditGovernanceInvalid("retention_days must be between 30 and 3650")
        async with self.session_factory.begin() as session:
            policy = await session.scalar(
                select(AuditRetentionPolicy)
                .where(AuditRetentionPolicy.tenant_id == tenant_id)
                .with_for_update()
            )
            if policy is None:
                policy = AuditRetentionPolicy(
                    tenant_id=tenant_id,
                    retention_days=retention_days,
                    is_enabled=is_enabled,
                    updated_by=actor_id,
                )
                session.add(policy)
            else:
                policy.retention_days = retention_days
                policy.is_enabled = is_enabled
                policy.updated_by = actor_id
            await session.flush()
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="audit.retention_policy.updated",
                resource_type="tenant",
                resource_id=tenant_id,
                metadata={
                    "retention_days": retention_days,
                    "is_enabled": is_enabled,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return _retention_result(policy)

    async def list_legal_holds(self, *, tenant_id: UUID) -> tuple[AuditLegalHoldResult, ...]:
        async with self.session_factory() as session:
            holds = (
                await session.scalars(
                    select(AuditLegalHold)
                    .where(AuditLegalHold.tenant_id == tenant_id)
                    .order_by(AuditLegalHold.created_at.desc(), AuditLegalHold.id.desc())
                )
            ).all()
        return tuple(_hold_result(hold) for hold in holds)

    async def create_legal_hold(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        name: str,
        reason: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        starts_at: datetime | None = None,
        expires_at: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditLegalHoldResult:
        _require_owner(role)
        if not 1 <= len(name.strip()) <= 200 or not 1 <= len(reason.strip()) <= 2000:
            raise AuditGovernanceInvalid("hold name and reason are required")
        if (resource_type is None) != (resource_id is None):
            raise AuditGovernanceInvalid("resource_type and resource_id must be supplied together")
        effective_start = _normalise_datetime(starts_at or datetime.now(UTC))
        effective_expiry = _normalise_datetime(expires_at) if expires_at is not None else None
        if effective_expiry is not None and effective_expiry <= effective_start:
            raise AuditGovernanceInvalid("expires_at must be after starts_at")
        async with self.session_factory.begin() as session:
            hold = AuditLegalHold(
                tenant_id=tenant_id,
                name=name.strip(),
                reason=reason.strip(),
                resource_type=resource_type,
                resource_id=resource_id,
                starts_at=effective_start,
                expires_at=effective_expiry,
                created_by=actor_id,
            )
            session.add(hold)
            await session.flush()
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="audit.legal_hold.created",
                resource_type="legal_hold",
                resource_id=hold.id,
                metadata={
                    "name": hold.name,
                    "resource_type": resource_type,
                    "resource_id": str(resource_id) if resource_id else None,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return _hold_result(hold)

    async def release_legal_hold(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        hold_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditLegalHoldResult:
        _require_owner(role)
        async with self.session_factory.begin() as session:
            hold = await session.scalar(
                select(AuditLegalHold)
                .where(
                    AuditLegalHold.id == hold_id,
                    AuditLegalHold.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if hold is None:
                raise AuditGovernanceNotFound()
            if hold.released_at is None:
                hold.released_at = datetime.now(UTC)
                hold.released_by = actor_id
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="audit.legal_hold.released",
                    resource_type="legal_hold",
                    resource_id=hold.id,
                    metadata={"name": hold.name},
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            return _hold_result(hold)

    async def retention_preview(
        self,
        *,
        tenant_id: UUID,
        now: datetime | None = None,
    ) -> AuditRetentionPreview:
        plan = await self.retention_plan(tenant_id=tenant_id, limit=1, now=now)
        return AuditRetentionPreview(
            cutoff_at=plan.cutoff_at,
            eligible_event_count=plan.eligible_event_count,
            protected_event_count=plan.protected_event_count,
        )

    async def retention_plan(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
        now: datetime | None = None,
    ) -> AuditRetentionPlan:
        if not 1 <= limit <= 500:
            raise ValueError("retention plan limit must be between 1 and 500")
        policy = await self.get_retention_policy(tenant_id=tenant_id)
        effective_now = _normalise_datetime(now or datetime.now(UTC))
        if not policy.is_enabled:
            return _retention_plan_result(
                policy=policy,
                cutoff_at=None,
                eligible_event_count=0,
                protected_event_count=0,
                eligible_event_ids=(),
            )
        cutoff = effective_now - timedelta(days=policy.retention_days)
        hold_exists = _hold_protects_event(tenant_id=tenant_id, now=effective_now)
        async with self.session_factory() as session:
            protected = int(
                await session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.occurred_at < cutoff,
                        hold_exists,
                    )
                )
                or 0
            )
            total = int(
                await session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.occurred_at < cutoff,
                    )
                )
                or 0
            )
            eligible_ids = tuple(
                (
                    await session.scalars(
                        select(AuditEvent.id)
                        .where(
                            AuditEvent.tenant_id == tenant_id,
                            AuditEvent.occurred_at < cutoff,
                            ~hold_exists,
                        )
                        .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
                        .limit(limit)
                    )
                ).all()
            )
        eligible_count = max(total - protected, 0)
        return _retention_plan_result(
            policy=policy,
            cutoff_at=cutoff,
            eligible_event_count=eligible_count,
            protected_event_count=protected,
            eligible_event_ids=eligible_ids,
        )

    async def archive_retention_plan(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        limit: int = 100,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditArchiveBatchResult:
        """Write one bounded, reversible archive snapshot; never delete source events."""
        _require_owner(role)
        if self.archive_store is None:
            raise AuditArchiveUnavailable("audit archive object store is not configured")
        archive_now = _archive_anchor(now or datetime.now(UTC))
        plan = await self.retention_plan(tenant_id=tenant_id, limit=limit, now=archive_now)
        if plan.cutoff_at is None or not plan.eligible_event_ids:
            raise AuditGovernanceInvalid("retention plan has no eligible events")

        async with self.session_factory() as session:
            existing = await session.scalar(
                select(AuditArchiveBatch).where(
                    AuditArchiveBatch.tenant_id == tenant_id,
                    AuditArchiveBatch.fingerprint == plan.fingerprint,
                )
            )
        if existing is not None:
            return _archive_result(existing)

        effective_now = archive_now
        async with self.session_factory() as session:
            events = tuple(
                (
                    await session.scalars(
                        select(AuditEvent)
                        .where(
                            AuditEvent.tenant_id == tenant_id,
                            AuditEvent.id.in_(plan.eligible_event_ids),
                            AuditEvent.occurred_at < plan.cutoff_at,
                            ~_hold_protects_event(tenant_id=tenant_id, now=effective_now),
                        )
                        .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
                    )
                ).all()
            )
        if not events:
            raise AuditGovernanceInvalid("retention plan has no currently eligible events")

        payload = {
            "schema_version": 1,
            "tenant_id": str(tenant_id),
            "cutoff_at": plan.cutoff_at.isoformat(),
            "plan_fingerprint": plan.fingerprint,
            "events": [
                {
                    "event_id": str(event.id),
                    "actor_id": str(event.actor_id) if event.actor_id else None,
                    "action": event.action,
                    "resource_type": event.resource_type,
                    "resource_id": str(event.resource_id) if event.resource_id else None,
                    "occurred_at": event.occurred_at.isoformat(),
                    "request_id": event.request_id,
                    "correlation_id": event.correlation_id,
                    "metadata": dict(event.event_metadata),
                    "schema_version": event.schema_version,
                }
                for event in events
            ],
        }
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(body) > self.max_archive_bytes:
            raise AuditGovernanceInvalid("retention archive exceeds the bounded snapshot size")
        content_sha256 = hashlib.sha256(body).hexdigest()
        object_key = (
            f"audit-archive/{tenant_id}/{plan.cutoff_at.strftime('%Y%m%dT%H%M%S%fZ')}"
            f"-{plan.fingerprint}.json"
        )
        stored = await self.archive_store.put_object(
            bucket=self.archive_bucket,
            key=object_key,
            body=body,
            content_type="application/json",
            metadata={
                "tenant-id": str(tenant_id),
                "fingerprint": plan.fingerprint,
            },
        )

        async with self.session_factory.begin() as session:
            batch = AuditArchiveBatch(
                tenant_id=tenant_id,
                fingerprint=plan.fingerprint,
                cutoff_at=plan.cutoff_at,
                archived_event_count=len(events),
                archived_event_ids=[str(event.id) for event in events],
                bucket=stored.bucket,
                object_key=stored.key,
                content_sha256=content_sha256,
                size_bytes=stored.size_bytes,
                created_by=actor_id,
            )
            try:
                async with session.begin_nested():
                    session.add(batch)
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(AuditArchiveBatch).where(
                        AuditArchiveBatch.tenant_id == tenant_id,
                        AuditArchiveBatch.fingerprint == plan.fingerprint,
                    )
                )
                if existing is None:
                    raise
                return _archive_result(existing)
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="audit.retention_archived",
                resource_type="audit_archive_batch",
                resource_id=batch.id,
                metadata={
                    "fingerprint": plan.fingerprint,
                    "archived_event_count": len(events),
                    "bucket": stored.bucket,
                    "object_key": stored.key,
                    "content_sha256": content_sha256,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return _archive_result(batch)

    async def list_archive_batches(
        self,
        *,
        tenant_id: UUID,
        limit: int = 25,
    ) -> tuple[AuditArchiveBatchResult, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("archive batch limit must be between 1 and 100")
        async with self.session_factory() as session:
            batches = (
                await session.scalars(
                    select(AuditArchiveBatch)
                    .where(AuditArchiveBatch.tenant_id == tenant_id)
                    .order_by(AuditArchiveBatch.created_at.desc(), AuditArchiveBatch.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(_archive_result(batch) for batch in batches)

    async def verify_archive_batch(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        batch_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditArchiveVerificationResult:
        _require_owner(role)
        if self.archive_store is None:
            raise AuditArchiveUnavailable("audit archive object store is not configured")
        async with self.session_factory() as session:
            batch = await session.scalar(
                select(AuditArchiveBatch).where(
                    AuditArchiveBatch.id == batch_id,
                    AuditArchiveBatch.tenant_id == tenant_id,
                )
            )
        if batch is None:
            raise AuditGovernanceNotFound()

        checked_at = datetime.now(UTC)
        try:
            head = await self.archive_store.head_object(bucket=batch.bucket, key=batch.object_key)
            actual_size = head.size_bytes
            body = (
                await self.archive_store.read_object(
                    bucket=batch.bucket,
                    key=batch.object_key,
                    expected_size=actual_size,
                )
                if actual_size > 0
                else b""
            )
        except ObjectStoreError as error:
            raise AuditArchiveUnavailable("audit archive object could not be read") from error

        actual_sha256 = hashlib.sha256(body).hexdigest()
        envelope_valid = False
        failure_reason: str | None = None
        try:
            envelope = json.loads(body.decode("utf-8"))
            events = envelope.get("events") if isinstance(envelope, dict) else None
            envelope_valid = (
                isinstance(envelope, dict)
                and envelope.get("schema_version") == 1
                and envelope.get("tenant_id") == str(tenant_id)
                and envelope.get("plan_fingerprint") == batch.fingerprint
                and isinstance(events, list)
                and len(events) == batch.archived_event_count
                and [item.get("event_id") for item in events if isinstance(item, dict)]
                == batch.archived_event_ids
            )
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            envelope_valid = False

        valid = (
            actual_size == batch.size_bytes
            and actual_sha256 == batch.content_sha256
            and head.metadata.get("sha256") == batch.content_sha256
            and envelope_valid
        )
        if not valid:
            if actual_size != batch.size_bytes:
                failure_reason = "size_mismatch"
            elif (
                actual_sha256 != batch.content_sha256
                or head.metadata.get("sha256") != batch.content_sha256
            ):
                failure_reason = "sha256_mismatch"
            elif not envelope_valid:
                failure_reason = "envelope_mismatch"
            else:
                failure_reason = "verification_failed"

        result = AuditArchiveVerificationResult(
            batch_id=batch.id,
            tenant_id=tenant_id,
            verified_at=checked_at,
            valid=valid,
            expected_sha256=batch.content_sha256,
            actual_sha256=actual_sha256,
            expected_size_bytes=batch.size_bytes,
            actual_size_bytes=actual_size,
            envelope_valid=envelope_valid,
            failure_reason=failure_reason,
        )
        async with self.session_factory.begin() as session:
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="audit.retention_archive.verified",
                resource_type="audit_archive_batch",
                resource_id=batch.id,
                metadata={
                    "valid": valid,
                    "failure_reason": failure_reason,
                    "actual_sha256": actual_sha256,
                    "actual_size_bytes": actual_size,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
        return result

    async def get_archive_download(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        batch_id: UUID,
        expires_in_seconds: int = 300,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditArchiveDownloadResult:
        _require_owner(role)
        if self.archive_store is None:
            raise AuditArchiveUnavailable("audit archive object store is not configured")
        if not 60 <= expires_in_seconds <= 900:
            raise AuditGovernanceInvalid(
                "archive download expiry must be between 60 and 900 seconds"
            )
        async with self.session_factory() as session:
            batch = await session.scalar(
                select(AuditArchiveBatch).where(
                    AuditArchiveBatch.id == batch_id,
                    AuditArchiveBatch.tenant_id == tenant_id,
                )
            )
        if batch is None:
            raise AuditGovernanceNotFound()
        try:
            head = await self.archive_store.head_object(bucket=batch.bucket, key=batch.object_key)
            if (
                head.size_bytes != batch.size_bytes
                or head.metadata.get("sha256") != batch.content_sha256
            ):
                raise AuditArchiveVerificationFailed("audit archive receipt does not match object")
            signed = await self.archive_store.presign_get(
                bucket=batch.bucket,
                key=batch.object_key,
                expires_in_seconds=expires_in_seconds,
            )
        except AuditGovernanceError:
            raise
        except (ObjectStoreChecksumMismatch, ObjectStoreProtocolError) as error:
            raise AuditArchiveVerificationFailed(
                "audit archive object failed integrity checks"
            ) from error
        except ObjectStoreError as error:
            raise AuditArchiveUnavailable("audit archive object could not be downloaded") from error

        async with self.session_factory.begin() as session:
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="audit.retention_archive.downloaded",
                resource_type="audit_archive_batch",
                resource_id=batch.id,
                metadata={
                    "bucket": batch.bucket,
                    "object_key": batch.object_key,
                    "expires_in_seconds": signed.expires_in_seconds,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
        return AuditArchiveDownloadResult(
            batch_id=batch.id,
            tenant_id=tenant_id,
            bucket=batch.bucket,
            object_key=batch.object_key,
            content_sha256=batch.content_sha256,
            size_bytes=batch.size_bytes,
            url=signed.url,
            expires_in_seconds=signed.expires_in_seconds,
        )


def _require_owner(role: str) -> None:
    if role != MembershipRole.OWNER.value:
        raise AuditGovernanceForbidden()


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _archive_anchor(value: datetime) -> datetime:
    """Use one UTC day boundary so retries share a stable retention fingerprint."""
    return _normalise_datetime(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _hold_protects_event(
    *,
    tenant_id: UUID,
    now: datetime | None = None,
) -> ColumnElement[bool]:
    effective_now = _normalise_datetime(now or datetime.now(UTC))
    return (
        select(AuditLegalHold.id)
        .where(
            AuditLegalHold.tenant_id == tenant_id,
            AuditLegalHold.starts_at <= effective_now,
            AuditLegalHold.released_at.is_(None),
            or_(
                AuditLegalHold.expires_at.is_(None),
                AuditLegalHold.expires_at > effective_now,
            ),
            or_(
                AuditLegalHold.resource_type.is_(None),
                AuditLegalHold.resource_type == AuditEvent.resource_type,
            ),
            or_(
                AuditLegalHold.resource_id.is_(None),
                AuditLegalHold.resource_id == AuditEvent.resource_id,
            ),
        )
        .correlate(AuditEvent)
        .exists()
    )


def _retention_plan_result(
    *,
    policy: AuditRetentionPolicyResult,
    cutoff_at: datetime | None,
    eligible_event_count: int,
    protected_event_count: int,
    eligible_event_ids: tuple[UUID, ...],
) -> AuditRetentionPlan:
    payload = {
        "tenant_id": str(policy.tenant_id),
        "retention_days": policy.retention_days,
        "is_enabled": policy.is_enabled,
        "cutoff_at": cutoff_at.isoformat() if cutoff_at else None,
        "eligible_event_count": eligible_event_count,
        "protected_event_count": protected_event_count,
        "eligible_event_ids": [str(event_id) for event_id in eligible_event_ids],
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return AuditRetentionPlan(
        policy=policy,
        cutoff_at=cutoff_at,
        eligible_event_count=eligible_event_count,
        protected_event_count=protected_event_count,
        eligible_event_ids=eligible_event_ids,
        fingerprint=fingerprint,
    )


def _retention_result(policy: AuditRetentionPolicy) -> AuditRetentionPolicyResult:
    return AuditRetentionPolicyResult(
        tenant_id=policy.tenant_id,
        retention_days=policy.retention_days,
        is_enabled=policy.is_enabled,
        updated_by=policy.updated_by,
    )


def _archive_result(batch: AuditArchiveBatch) -> AuditArchiveBatchResult:
    return AuditArchiveBatchResult(
        batch_id=batch.id,
        tenant_id=batch.tenant_id,
        cutoff_at=batch.cutoff_at,
        archived_event_count=batch.archived_event_count,
        fingerprint=batch.fingerprint,
        bucket=batch.bucket,
        object_key=batch.object_key,
        content_sha256=batch.content_sha256,
        size_bytes=batch.size_bytes,
        created_by=batch.created_by,
        created_at=batch.created_at,
    )


def _hold_result(hold: AuditLegalHold) -> AuditLegalHoldResult:
    return AuditLegalHoldResult(
        hold_id=hold.id,
        tenant_id=hold.tenant_id,
        name=hold.name,
        reason=hold.reason,
        resource_type=hold.resource_type,
        resource_id=hold.resource_id,
        starts_at=hold.starts_at,
        expires_at=hold.expires_at,
        released_at=hold.released_at,
        created_by=hold.created_by,
        released_by=hold.released_by,
    )
