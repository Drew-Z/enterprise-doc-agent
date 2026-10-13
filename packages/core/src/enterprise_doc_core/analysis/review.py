from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.analysis.evidence import validate_value_type
from enterprise_doc_core.analysis.execution import (
    AnalysisExecutionService,
    append_event,
    lock_run,
    mark_downstream_stale,
)
from enterprise_doc_core.analysis.execution_models import (
    AnalysisApproval,
    AnalysisArtifact,
    AnalysisCellRevision,
    AnalysisReview,
    AnalysisRun,
    AnalysisUnit,
)
from enterprise_doc_core.analysis.execution_schemas import CellResult, ReviewView
from enterprise_doc_core.analysis.export import CONTENT_TYPE, render_analysis_workbook
from enterprise_doc_core.analysis.gateway import AnalysisGateway
from enterprise_doc_core.analysis.review_schemas import (
    ArtifactApprovalInput,
    ArtifactApprovalView,
    ArtifactDownloadView,
    ArtifactView,
    CellRevisionView,
    ExportInput,
    ManualRevisionInput,
    ReviewInput,
)
from enterprise_doc_core.analysis.schemas import SnapshotView, canonical_hash
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.config import (
    AppEnvironment,
    FoundationSettings,
    ProviderUsageSettings,
)
from enterprise_doc_core.object_store import ArtifactObjectStore
from enterprise_doc_core.object_store.errors import ObjectStoreError


def build_review_delivery_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: FoundationSettings,
    artifact_store: ArtifactObjectStore | None = None,
) -> AnalysisReviewDeliveryService:
    try:
        gateway = AnalysisGateway(model=settings.model, settings=settings.analysis)
    except AnalysisError:
        gateway = None
    return AnalysisReviewDeliveryService(
        session_factory=session_factory,
        gateway=gateway,
        app_env=settings.app_env,
        provider_settings=settings.provider_usage,
        artifact_store=artifact_store,
        artifact_bucket=settings.object_store.artifacts_bucket,
        download_ttl_seconds=settings.object_store.presign_ttl_seconds,
    )


class AnalysisReviewDeliveryService(AnalysisExecutionService):
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: AnalysisGateway | None = None,
        app_env: AppEnvironment = AppEnvironment.LOCAL,
        provider_settings: ProviderUsageSettings | None = None,
        artifact_store: ArtifactObjectStore | None = None,
        artifact_bucket: str = "artifacts",
        download_ttl_seconds: int = 300,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            gateway=gateway,
            app_env=app_env,
            provider_settings=provider_settings,
        )
        if not artifact_bucket or not 1 <= download_ttl_seconds <= 3600:
            raise ValueError("invalid artifact delivery configuration")
        self.artifact_store = artifact_store
        self.artifact_bucket = artifact_bucket
        self.download_ttl_seconds = download_ttl_seconds

    async def create_cell_revision(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        unit_id: UUID,
        expected_revision: int,
        idempotency_key: str,
        request: ManualRevisionInput,
    ) -> CellRevisionView:
        self._validate_key(idempotency_key)
        fingerprint = self._payload_hash(
            {
                "expected_revision": expected_revision,
                "request": request.model_dump(mode="json"),
            }
        )
        async with self.session_factory.begin() as session:
            run, unit = await self._lock_authorized_unit(
                session, tenant_id=tenant_id, actor_id=actor_id, unit_id=unit_id
            )
            replay = await session.scalar(
                select(AnalysisCellRevision).where(
                    AnalysisCellRevision.unit_id == unit_id,
                    AnalysisCellRevision.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnalysisError("analysis_idempotency_conflict")
                return await self._revision_view(session, replay)

            current = await self._current_revision(session, unit_id)
            if current is None and (
                expected_revision != 0 or unit.status not in {"failed", "cancelled", "expired"}
            ):
                raise AnalysisError("analysis_result_required")
            if current is not None:
                self._validate_revision(current)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                raise AnalysisError("analysis_revision_conflict")
            snapshot = await self._authorized_snapshot(session, run, actor_id)
            field = next(
                (item for item in snapshot.payload.spec.fields if item.id == unit.field_id), None
            )
            if field is None:
                raise AnalysisError("analysis_source_invalid")
            validate_value_type(request.value, field)
            result = CellResult(
                action=request.action,
                value=request.value,
                evidence_state=request.evidence_state,
                reason=request.reason,
                evidence=(),
                calculations=(),
                value_origin="user_provided",
            )
            row = AnalysisCellRevision(
                tenant_id=tenant_id,
                unit_id=unit_id,
                revision=current_revision + 1,
                result=result.model_dump(mode="json"),
                content_hash=canonical_hash(result),
                author_type="user",
                actor_id=actor_id,
                reason=request.reason,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                supersedes_id=current.id if current is not None else None,
            )
            session.add(row)
            await session.flush()
            await mark_downstream_stale(session, run, unit)
            await append_event(session, run, "cell.revised", unit)
            await self._audit(session, tenant_id, actor_id, "analysis.cell.revised", row.id)
            return await self._revision_view(session, row)

    async def review_cell_revision(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        unit_id: UUID,
        idempotency_key: str,
        request: ReviewInput,
    ) -> ReviewView:
        self._validate_key(idempotency_key)
        fingerprint = self._payload_hash(request.model_dump(mode="json"))
        async with self.session_factory.begin() as session:
            run, unit = await self._lock_authorized_unit(
                session, tenant_id=tenant_id, actor_id=actor_id, unit_id=unit_id
            )
            replay = await session.scalar(
                select(AnalysisReview).where(
                    AnalysisReview.unit_id == unit_id,
                    AnalysisReview.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnalysisError("analysis_idempotency_conflict")
                return self._review_view(replay)

            current = await self._current_revision(session, unit_id)
            if current is None:
                raise AnalysisError("analysis_result_required")
            self._validate_revision(current)
            if current.revision != request.revision or current.content_hash != request.content_hash:
                raise AnalysisError("analysis_review_revision_conflict")
            previous = await session.scalar(
                select(AnalysisReview).where(AnalysisReview.cell_revision_id == current.id)
            )
            if previous is not None:
                raise AnalysisError("analysis_review_conflict")
            row = AnalysisReview(
                tenant_id=tenant_id,
                unit_id=unit_id,
                cell_revision_id=current.id,
                revision=current.revision,
                content_hash=current.content_hash,
                decision=request.decision,
                reason=request.reason,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
            session.add(row)
            await session.flush()
            await append_event(session, run, "cell.reviewed", unit)
            await self._audit(session, tenant_id, actor_id, "analysis.cell.reviewed", row.id)
            return self._review_view(row)

    async def cell_revisions(
        self, *, tenant_id: UUID, actor_id: UUID, unit_id: UUID
    ) -> tuple[CellRevisionView, ...]:
        async with self.session_factory() as session:
            await self._authorized_unit(
                session, tenant_id=tenant_id, actor_id=actor_id, unit_id=unit_id
            )
            rows = (
                await session.scalars(
                    select(AnalysisCellRevision)
                    .where(
                        AnalysisCellRevision.tenant_id == tenant_id,
                        AnalysisCellRevision.unit_id == unit_id,
                    )
                    .order_by(AnalysisCellRevision.revision.desc())
                )
            ).all()
            return tuple([await self._revision_view(session, row) for row in rows])

    async def create_export(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
        idempotency_key: str,
        request: ExportInput,
    ) -> ArtifactView:
        self._validate_key(idempotency_key)
        store = self._artifact_store()
        fingerprint = self._payload_hash(
            {"run_id": str(run_id), "request": request.model_dump(mode="json")}
        )
        async with self.session_factory.begin() as session:
            await lock_run(session, run_id)
            run = await self._find_run(session, tenant_id, run_id)
            snapshot = await self._authorized_snapshot(session, run, actor_id)
            replay = await session.scalar(
                select(AnalysisArtifact).where(
                    AnalysisArtifact.run_id == run_id,
                    AnalysisArtifact.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnalysisError("analysis_idempotency_conflict")
                if replay.status in {"ready", "approved"}:
                    return self._artifact_view(replay)
                if replay.status == "failed":
                    raise AnalysisError("analysis_artifact_failed")
                artifact = replay
                manifest = dict(replay.matrix_manifest)
            else:
                if run.status not in {"completed", "partial", "failed", "expired", "cancelled"}:
                    raise AnalysisError("analysis_export_busy")
                manifest = await self._matrix_manifest(
                    session,
                    run=run,
                    snapshot=snapshot,
                    mode=request.mode,
                )
                matrix_revision = (
                    await session.scalar(
                        select(func.max(AnalysisArtifact.matrix_revision)).where(
                            AnalysisArtifact.run_id == run_id
                        )
                    )
                    or 0
                ) + 1
                matrix_hash = self._payload_hash(manifest)
                filename = f"analysis-{run.id}-{request.mode}-r{matrix_revision}.xlsx"
                artifact = AnalysisArtifact(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    mode=request.mode,
                    status="writing",
                    format="xlsx",
                    matrix_revision=matrix_revision,
                    matrix_hash=matrix_hash,
                    matrix_manifest=manifest,
                    source_manifest_hash=run.source_manifest_hash,
                    cell_revision_ids=[
                        str(cell["revision_id"])
                        for cell in manifest["cells"]
                        if cell["revision_id"] is not None
                    ],
                    object_bucket=self.artifact_bucket,
                    object_key=(
                        f"analysis/{tenant_id}/{run_id}/r{matrix_revision}-{matrix_hash}.xlsx"
                    ),
                    filename=filename,
                    content_type=CONTENT_TYPE,
                    created_by=actor_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(artifact)
                await session.flush()
                await append_event(session, run, "artifact.frozen")
                await self._audit(
                    session, tenant_id, actor_id, "analysis.artifact.frozen", artifact.id
                )
            artifact_id = artifact.id
            bucket = artifact.object_bucket
            key = artifact.object_key
            expected_matrix_hash = artifact.matrix_hash
            expected_source_hash = artifact.source_manifest_hash

        try:
            body = await asyncio.to_thread(render_analysis_workbook, manifest)
            local_hash = hashlib.sha256(body).hexdigest()
            stored = await store.put_object(
                bucket=bucket,
                key=key,
                body=body,
                content_type=CONTENT_TYPE,
                metadata={
                    "matrix-hash": expected_matrix_hash,
                    "source-manifest-hash": expected_source_hash,
                    "artifact-id": str(artifact_id),
                },
            )
            if (
                stored.content_sha256 != local_hash
                or stored.size_bytes != len(body)
                or stored.content_type != CONTENT_TYPE
            ):
                raise AnalysisError("analysis_artifact_invalid")
            head = await store.head_object(bucket=bucket, key=key)
            if (
                head.size_bytes != len(body)
                or head.metadata.get("sha256") != local_hash
                or head.metadata.get("matrix-hash") != expected_matrix_hash
                or head.metadata.get("source-manifest-hash") != expected_source_hash
            ):
                raise AnalysisError("analysis_artifact_invalid")
            async with self.session_factory.begin() as session:
                row = await session.scalar(
                    select(AnalysisArtifact)
                    .where(
                        AnalysisArtifact.id == artifact_id,
                        AnalysisArtifact.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if row is None or row.status != "writing":
                    raise AnalysisError("analysis_artifact_conflict")
                run = await self._find_run(session, tenant_id, row.run_id)
                await self._authorized_snapshot(session, run, actor_id)
                row.content_hash = local_hash
                row.size_bytes = len(body)
                row.verified_at = datetime.now(UTC)
                row.status = "ready"
                await append_event(session, run, "artifact.ready")
                return self._artifact_view(row)
        except (AnalysisError, ObjectStoreError):
            async with self.session_factory.begin() as session:
                row = await session.scalar(
                    select(AnalysisArtifact)
                    .where(
                        AnalysisArtifact.id == artifact_id,
                        AnalysisArtifact.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if row is not None and row.status == "writing":
                    row.status = "failed"
            raise

    async def list_artifacts(
        self, *, tenant_id: UUID, actor_id: UUID, run_id: UUID
    ) -> tuple[ArtifactView, ...]:
        async with self.session_factory() as session:
            run = await self._find_run(session, tenant_id, run_id)
            await self._authorized_snapshot(session, run, actor_id)
            rows = (
                await session.scalars(
                    select(AnalysisArtifact)
                    .where(
                        AnalysisArtifact.tenant_id == tenant_id,
                        AnalysisArtifact.run_id == run_id,
                        AnalysisArtifact.status.in_(("ready", "approved")),
                    )
                    .order_by(AnalysisArtifact.matrix_revision.desc())
                )
            ).all()
            result = []
            for row in rows:
                if self._payload_hash(row.matrix_manifest) != row.matrix_hash:
                    raise AnalysisError("analysis_artifact_invalid")
                result.append(self._artifact_view(row))
            return tuple(result)

    async def approve_artifact(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
        idempotency_key: str,
        request: ArtifactApprovalInput,
    ) -> ArtifactApprovalView:
        self._validate_key(idempotency_key)
        fingerprint = self._payload_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        if request.expires_at.tzinfo is None or request.expires_at <= now:
            raise AnalysisError("analysis_approval_expired")
        async with self.session_factory.begin() as session:
            artifact, run = await self._locked_authorized_artifact(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                artifact_id=artifact_id,
            )
            replay = await session.scalar(
                select(AnalysisApproval).where(
                    AnalysisApproval.artifact_id == artifact_id,
                    AnalysisApproval.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnalysisError("analysis_idempotency_conflict")
                return self._approval_view(replay)
            if artifact.mode != "reviewed" or artifact.status != "ready":
                raise AnalysisError("analysis_artifact_not_approvable")
            if (
                artifact.content_hash != request.artifact_hash
                or artifact.source_manifest_hash != request.source_manifest_hash
                or artifact.matrix_hash != request.matrix_hash
            ):
                raise AnalysisError("analysis_approval_target_conflict")
            if await session.scalar(
                select(AnalysisApproval.id).where(AnalysisApproval.artifact_id == artifact_id)
            ):
                raise AnalysisError("analysis_approval_conflict")
            approval = AnalysisApproval(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                artifact_hash=request.artifact_hash,
                source_manifest_hash=request.source_manifest_hash,
                matrix_hash=request.matrix_hash,
                operation=request.operation,
                decision=request.decision,
                reason=request.reason,
                actor_id=actor_id,
                expires_at=request.expires_at,
                decided_at=now,
                consumed_at=now if request.decision == "approved" else None,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
            session.add(approval)
            if request.decision == "approved":
                artifact.status = "approved"
            await session.flush()
            event_type = (
                "artifact.approved" if request.decision == "approved" else "artifact.rejected"
            )
            await append_event(session, run, event_type)
            await self._audit(
                session, tenant_id, actor_id, "analysis.artifact.decided", approval.id
            )
            return self._approval_view(approval)

    async def download_artifact(
        self, *, tenant_id: UUID, actor_id: UUID, artifact_id: UUID
    ) -> ArtifactDownloadView:
        store = self._artifact_store()
        async with self.session_factory() as session:
            artifact, _ = await self._authorized_artifact(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                artifact_id=artifact_id,
            )
            await self._require_downloadable(session, artifact)
            target = self._artifact_target(artifact)
        try:
            head = await store.head_object(bucket=target[0], key=target[1])
        except ObjectStoreError as error:
            raise AnalysisError("analysis_artifact_store_unavailable") from error
        if (
            head.size_bytes != target[4]
            or head.content_type != target[3]
            or head.metadata.get("sha256") != target[2]
            or head.metadata.get("matrix-hash") != target[5]
            or head.metadata.get("source-manifest-hash") != target[6]
        ):
            raise AnalysisError("analysis_artifact_invalid")
        async with self.session_factory() as session:
            current, _ = await self._authorized_artifact(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                artifact_id=artifact_id,
            )
            await self._require_downloadable(session, current)
            if self._artifact_target(current) != target:
                raise AnalysisError("analysis_artifact_conflict")
        try:
            signed = await store.presign_get(
                bucket=target[0], key=target[1], expires_in_seconds=self.download_ttl_seconds
            )
        except ObjectStoreError as error:
            raise AnalysisError("analysis_artifact_store_unavailable") from error
        return ArtifactDownloadView(
            artifact_id=artifact_id,
            filename=target[7],
            content_type=target[3],
            content_hash=target[2],
            size_bytes=target[4],
            url=signed.url,
            expires_in_seconds=signed.expires_in_seconds,
        )

    async def _matrix_manifest(
        self,
        session: AsyncSession,
        *,
        run: AnalysisRun,
        snapshot: SnapshotView,
        mode: Literal["draft", "reviewed"],
    ) -> dict[str, Any]:
        units = (
            await session.scalars(
                select(AnalysisUnit)
                .where(AnalysisUnit.run_id == run.id)
                .order_by(AnalysisUnit.ordinal)
            )
        ).all()
        objects = {item.id: item.name for item in snapshot.payload.spec.objects}
        fields = {item.id: item.name for item in snapshot.payload.spec.fields}
        cells: list[dict[str, object]] = []
        for unit in units:
            revision = await self._current_revision(session, unit.id)
            review = (
                await session.scalar(
                    select(AnalysisReview).where(AnalysisReview.cell_revision_id == revision.id)
                )
                if revision is not None
                else None
            )
            if revision is not None:
                self._validate_revision(revision)
            if mode == "reviewed" and (
                unit.freshness != "current"
                or revision is None
                or review is None
                or review.decision != "approved"
            ):
                raise AnalysisError("analysis_review_required")
            cells.append(
                {
                    "unit_id": str(unit.id),
                    "object_id": unit.object_id,
                    "object_name": objects[unit.object_id],
                    "field_id": unit.field_id,
                    "field_name": fields[unit.field_id],
                    "status": unit.status,
                    "freshness": unit.freshness,
                    "invalidation_reasons": list(unit.invalidation_reasons or ()),
                    "error_code": unit.error_code,
                    "revision_id": str(revision.id) if revision is not None else None,
                    "revision": revision.revision if revision is not None else None,
                    "content_hash": revision.content_hash if revision is not None else None,
                    "author_type": revision.author_type if revision is not None else None,
                    "result": dict(revision.result) if revision is not None else None,
                    "review": (
                        {
                            "id": str(review.id),
                            "decision": review.decision,
                            "reason": review.reason,
                            "actor_id": str(review.actor_id),
                            "content_hash": review.content_hash,
                        }
                        if review is not None
                        else None
                    ),
                }
            )
        return {
            "schema_version": "analysis.matrix.v1",
            "mode": mode,
            "run_id": str(run.id),
            "snapshot_id": str(run.snapshot_id),
            "title": snapshot.payload.title,
            "source_manifest_hash": run.source_manifest_hash,
            "objects": [
                {"id": item.id, "name": item.name} for item in snapshot.payload.spec.objects
            ],
            "fields": [{"id": item.id, "name": item.name} for item in snapshot.payload.spec.fields],
            "sources": [item.model_dump(mode="json") for item in snapshot.payload.sources],
            "cells": cells,
        }

    async def _locked_authorized_artifact(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
    ) -> tuple[AnalysisArtifact, AnalysisRun]:
        run_id = await session.scalar(
            select(AnalysisArtifact.run_id).where(
                AnalysisArtifact.id == artifact_id,
                AnalysisArtifact.tenant_id == tenant_id,
            )
        )
        if run_id is None:
            raise AnalysisError("analysis_not_found")
        await lock_run(session, run_id)
        return await self._authorized_artifact(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            artifact_id=artifact_id,
            lock=True,
        )

    async def _authorized_artifact(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
        lock: bool = False,
    ) -> tuple[AnalysisArtifact, AnalysisRun]:
        statement = select(AnalysisArtifact).where(
            AnalysisArtifact.id == artifact_id,
            AnalysisArtifact.tenant_id == tenant_id,
        )
        artifact = await session.scalar(statement.with_for_update() if lock else statement)
        if artifact is None:
            raise AnalysisError("analysis_not_found")
        run = await self._find_run(session, tenant_id, artifact.run_id)
        await self._authorized_snapshot(session, run, actor_id)
        if (
            artifact.source_manifest_hash != run.source_manifest_hash
            or self._payload_hash(artifact.matrix_manifest) != artifact.matrix_hash
        ):
            raise AnalysisError("analysis_artifact_invalid")
        return artifact, run

    async def _require_downloadable(
        self, session: AsyncSession, artifact: AnalysisArtifact
    ) -> None:
        if (
            artifact.content_hash is None
            or artifact.size_bytes is None
            or artifact.status not in {"ready", "approved"}
        ):
            raise AnalysisError("analysis_artifact_unavailable")
        if artifact.mode == "reviewed":
            approval = await session.scalar(
                select(AnalysisApproval).where(
                    AnalysisApproval.artifact_id == artifact.id,
                    AnalysisApproval.artifact_hash == artifact.content_hash,
                    AnalysisApproval.source_manifest_hash == artifact.source_manifest_hash,
                    AnalysisApproval.matrix_hash == artifact.matrix_hash,
                    AnalysisApproval.operation == "deliver",
                    AnalysisApproval.decision == "approved",
                    AnalysisApproval.consumed_at.is_not(None),
                )
            )
            if approval is None or approval.expires_at <= datetime.now(UTC):
                raise AnalysisError("analysis_approval_required")

    @staticmethod
    def _artifact_target(
        artifact: AnalysisArtifact,
    ) -> tuple[str, str, str, str, int, str, str, str]:
        if artifact.content_hash is None or artifact.size_bytes is None:
            raise AnalysisError("analysis_artifact_unavailable")
        return (
            artifact.object_bucket,
            artifact.object_key,
            artifact.content_hash,
            artifact.content_type,
            artifact.size_bytes,
            artifact.matrix_hash,
            artifact.source_manifest_hash,
            artifact.filename,
        )

    def _artifact_store(self) -> ArtifactObjectStore:
        if self.artifact_store is None:
            raise AnalysisError("analysis_artifact_store_unavailable")
        return self.artifact_store

    @staticmethod
    def _artifact_view(row: AnalysisArtifact) -> ArtifactView:
        return ArtifactView(
            id=row.id,
            run_id=row.run_id,
            mode=cast(Literal["draft", "reviewed"], row.mode),
            status=cast(Literal["writing", "ready", "approved", "failed"], row.status),
            matrix_revision=row.matrix_revision,
            matrix_hash=row.matrix_hash,
            source_manifest_hash=row.source_manifest_hash,
            cell_revision_ids=tuple(UUID(value) for value in row.cell_revision_ids),
            object_bucket=row.object_bucket,
            object_key=row.object_key,
            filename=row.filename,
            content_type=row.content_type,
            content_hash=row.content_hash,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
        )

    @staticmethod
    def _approval_view(row: AnalysisApproval) -> ArtifactApprovalView:
        return ArtifactApprovalView(
            id=row.id,
            artifact_id=row.artifact_id,
            artifact_hash=row.artifact_hash,
            source_manifest_hash=row.source_manifest_hash,
            matrix_hash=row.matrix_hash,
            operation="deliver",
            decision=cast(Literal["approved", "rejected"], row.decision),
            reason=row.reason,
            actor_id=row.actor_id,
            expires_at=row.expires_at,
            decided_at=row.decided_at,
            consumed_at=row.consumed_at,
        )

    async def _lock_authorized_unit(
        self, session: AsyncSession, *, tenant_id: UUID, actor_id: UUID, unit_id: UUID
    ) -> tuple[AnalysisRun, AnalysisUnit]:
        run_id = await session.scalar(
            select(AnalysisUnit.run_id).where(
                AnalysisUnit.id == unit_id, AnalysisUnit.tenant_id == tenant_id
            )
        )
        if run_id is None:
            raise AnalysisError("analysis_not_found")
        await lock_run(session, run_id)
        return await self._authorized_unit(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            unit_id=unit_id,
            lock=True,
        )

    async def _authorized_unit(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        unit_id: UUID,
        lock: bool = False,
    ) -> tuple[AnalysisRun, AnalysisUnit]:
        statement = select(AnalysisUnit).where(
            AnalysisUnit.id == unit_id, AnalysisUnit.tenant_id == tenant_id
        )
        unit = await session.scalar(statement.with_for_update() if lock else statement)
        if unit is None:
            raise AnalysisError("analysis_not_found")
        run = await self._find_run(session, tenant_id, unit.run_id)
        await self._authorized_snapshot(session, run, actor_id)
        return run, unit

    @staticmethod
    async def _current_revision(
        session: AsyncSession, unit_id: UUID
    ) -> AnalysisCellRevision | None:
        return cast(
            AnalysisCellRevision | None,
            await session.scalar(
                select(AnalysisCellRevision)
                .where(AnalysisCellRevision.unit_id == unit_id)
                .order_by(AnalysisCellRevision.revision.desc())
                .limit(1)
            ),
        )

    async def _revision_view(
        self, session: AsyncSession, row: AnalysisCellRevision
    ) -> CellRevisionView:
        self._validate_revision(row)
        review = await session.scalar(
            select(AnalysisReview).where(AnalysisReview.cell_revision_id == row.id)
        )
        return CellRevisionView(
            id=row.id,
            unit_id=row.unit_id,
            revision=row.revision,
            result=CellResult.model_validate(row.result),
            content_hash=row.content_hash,
            author_type=cast(Literal["model", "user"], row.author_type),
            actor_id=row.actor_id,
            reason=row.reason,
            created_at=row.created_at,
            supersedes_id=row.supersedes_id,
            review=self._review_view(review) if review is not None else None,
        )

    def _validate_revision(self, row: AnalysisCellRevision) -> None:
        try:
            CellResult.model_validate(row.result)
        except ValueError as error:
            raise AnalysisError("analysis_result_invalid") from error
        if self._payload_hash(row.result) != row.content_hash:
            raise AnalysisError("analysis_result_invalid")

    @staticmethod
    def _validate_key(value: str) -> None:
        if not 1 <= len(value) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise AnalysisError("analysis_idempotency_invalid")


__all__ = ["AnalysisReviewDeliveryService", "build_review_delivery_service"]
