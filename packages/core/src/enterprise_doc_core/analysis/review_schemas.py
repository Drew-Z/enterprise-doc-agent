from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from enterprise_doc_core.analysis.execution_schemas import CellResult, ReviewView, Value
from enterprise_doc_core.analysis.schemas import AnalysisModel

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ManualRevisionInput(AnalysisModel):
    action: Literal["final", "abstain"]
    value: Value = None
    evidence_state: Literal["conflicting", "insufficient", "unreadable"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    value_origin: Literal["user_provided"] = "user_provided"

    @model_validator(mode="after")
    def coherent(self) -> ManualRevisionInput:
        if isinstance(self.value, str) and (not self.value or len(self.value) > 4000):
            raise ValueError("invalid value length")
        if isinstance(self.value, tuple) and not 1 <= len(self.value) <= 30:
            raise ValueError("invalid list length")
        if self.action == "final" and self.value is None:
            raise ValueError("final revisions require a value")
        if self.action != "final" and self.value is not None:
            raise ValueError("unresolved revisions must have a null value")
        return self


class ReviewInput(AnalysisModel):
    revision: int = Field(ge=1)
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    decision: Literal["approved", "changes_requested", "rejected"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class CellRevisionView(AnalysisModel):
    id: UUID
    unit_id: UUID
    revision: int = Field(ge=1)
    result: CellResult
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    author_type: Literal["model", "user"]
    actor_id: UUID | None
    reason: str
    created_at: datetime
    supersedes_id: UUID | None
    review: ReviewView | None = None


class ExportInput(AnalysisModel):
    mode: Literal["draft", "reviewed"]


class ArtifactView(AnalysisModel):
    id: UUID
    run_id: UUID
    mode: Literal["draft", "reviewed"]
    status: Literal["writing", "ready", "approved", "failed"]
    format: Literal["xlsx"] = "xlsx"
    matrix_revision: int = Field(ge=1)
    matrix_hash: Hash
    source_manifest_hash: Hash
    cell_revision_ids: tuple[UUID, ...]
    object_bucket: str
    object_key: str
    filename: str
    content_type: str
    content_hash: Hash | None
    size_bytes: int | None = Field(default=None, ge=1)
    created_at: datetime


class ArtifactApprovalInput(AnalysisModel):
    artifact_hash: Hash
    source_manifest_hash: Hash
    matrix_hash: Hash
    operation: Literal["deliver"] = "deliver"
    decision: Literal["approved", "rejected"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    expires_at: datetime


class ArtifactApprovalView(AnalysisModel):
    id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    source_manifest_hash: Hash
    matrix_hash: Hash
    operation: Literal["deliver"]
    decision: Literal["approved", "rejected"]
    reason: str
    actor_id: UUID
    expires_at: datetime
    decided_at: datetime
    consumed_at: datetime | None


class ArtifactDownloadView(AnalysisModel):
    artifact_id: UUID
    filename: str
    content_type: str
    content_hash: Hash
    size_bytes: int = Field(ge=1)
    url: str
    expires_in_seconds: int = Field(ge=1)


__all__ = [
    "ArtifactApprovalInput",
    "ArtifactApprovalView",
    "ArtifactDownloadView",
    "ArtifactView",
    "CellRevisionView",
    "ExportInput",
    "ManualRevisionInput",
    "ReviewInput",
]
