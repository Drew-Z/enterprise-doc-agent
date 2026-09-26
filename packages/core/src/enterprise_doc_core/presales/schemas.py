from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

Status = Literal[
    "supported", "conditional", "contradicted", "insufficient_evidence", "conflicting_evidence"
]
TextItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class PresalesModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel, str_strip_whitespace=True
    )


class SourceInput(PresalesModel):
    version_id: UUID
    applicability: str = Field(min_length=1, max_length=500)


class RequirementInput(PresalesModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=2000)
    source_location: str = Field(default="", max_length=300)


class CreatePacket(PresalesModel):
    title: str = Field(min_length=1, max_length=160)
    sources: list[SourceInput] = Field(min_length=1, max_length=6)
    requirements: list[RequirementInput] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_inputs(self) -> Self:
        if len({s.version_id for s in self.sources}) != len(self.sources):
            raise ValueError("source versions must be unique")
        if len({r.key for r in self.requirements}) != len(self.requirements):
            raise ValueError("requirement keys must be unique")
        return self


class SourceSnapshot(SourceInput):
    document_id: UUID
    generation_id: UUID
    filename: str
    version_number: int
    latest_version_number: int
    content_sha256: str


class CitationInput(PresalesModel):
    chunk_id: UUID
    document_version_id: UUID
    excerpt: str = Field(min_length=1, max_length=600)


class PrerequisiteAssessment(PresalesModel):
    condition: TextItem
    state: Literal["met", "unmet", "unknown"]
    citation_indexes: list[Annotated[int, Field(ge=0, lt=12, strict=True)]] = Field(
        min_length=1, max_length=12
    )

    @model_validator(mode="after")
    def unique_citations(self) -> Self:
        if len(set(self.citation_indexes)) != len(self.citation_indexes):
            raise ValueError("prerequisite references must not contain duplicates")
        return self


def prerequisite_conditions(items: list[PrerequisiteAssessment]) -> list[str]:
    return list(dict.fromkeys(item.condition for item in items if item.state != "met"))


class ResponseText(PresalesModel):
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    conditions: list[TextItem] = Field(default_factory=list, max_length=12)
    missing_information: list[TextItem] = Field(default_factory=list, max_length=12)
    # None means the older contract did not record an assessment; [] is explicit.
    prerequisites: list[PrerequisiteAssessment] | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def required_details(self) -> Self:
        if self.prerequisites is not None and self.conditions != prerequisite_conditions(
            self.prerequisites
        ):
            raise ValueError("conditions must match outstanding prerequisites")
        if self.status == "conditional" and not self.conditions:
            raise ValueError("conditional response requires conditions")
        if self.status == "insufficient_evidence" and not self.missing_information:
            raise ValueError("insufficient evidence requires a follow-up question")
        if self.status == "supported" and self.conditions:
            raise ValueError("unmet conditions require conditional status")
        return self

    def validate_prerequisite_citations(self, count: int) -> None:
        if any(
            index >= count for item in self.prerequisites or [] for index in item.citation_indexes
        ):
            raise ValueError("prerequisite references must identify saved evidence")


class ModelDraft(ResponseText):
    citations: list[CitationInput] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def required_citations(self) -> Self:
        self.validate_prerequisite_citations(len(self.citations))
        if self.status != "insufficient_evidence" and not self.citations:
            raise ValueError("this status requires evidence")
        if (
            self.status == "conflicting_evidence"
            and len({c.document_version_id for c in self.citations}) < 2
        ):
            raise ValueError("conflict requires both source versions")
        return self


class Evidence(CitationInput):
    filename: str
    page_number: int | None
    heading: str | None
    start_offset: int
    end_offset: int


class RetrievalNote(PresalesModel):
    version_id: UUID
    retrieved_count: int
    used_count: int
    truncated: bool


class SavedDraft(ResponseText):
    citations: list[Evidence]
    retrieval: list[RetrievalNote]

    @model_validator(mode="after")
    def bound_prerequisites(self) -> Self:
        self.validate_prerequisite_citations(len(self.citations))
        return self


class ReviewInput(ResponseText):
    expected_revision: int = Field(ge=1, strict=True)
    note: str = Field(default="", max_length=1000)


class SavedReview(ResponseText):
    revision: int
    note: str
    actor_id: UUID
    reviewed_at: datetime


class AttemptView(PresalesModel):
    id: UUID
    number: int
    state: Literal["queued", "running", "recovering", "succeeded", "failed", "expired"]
    error_code: str | None
    model_provider: str
    model_name: str | None
    provider_request_count: int | None = Field(ge=0, le=2)
    provenance: dict[str, str | None]
    usage: dict[str, int | None] | None
    created_at: datetime
    finished_at: datetime | None
    deadline_at: datetime


class RowView(PresalesModel):
    id: UUID
    requirement: RequirementInput
    revision: int
    state: Literal["pending", "queued", "running", "recovering", "drafted", "failed"]
    draft: SavedDraft | None
    review: SavedReview | None
    review_history: list[SavedReview]
    attempts: list[AttemptView]


class PacketSummary(PresalesModel):
    id: UUID
    title: str
    created_at: datetime
    row_count: int
    stale_sources: bool = False


class PacketView(PacketSummary):
    sources: list[SourceSnapshot]
    rows: list[RowView]
    generation_mode: Literal["synchronous", "background"] = "synchronous"


class BatchGenerateInput(PresalesModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_rows(self) -> Self:
        if len(set(self.row_ids)) != len(self.row_ids):
            raise ValueError("row IDs must be unique")
        return self


class RowRejection(PresalesModel):
    row_id: UUID
    code: str


class BatchGenerateResult(PresalesModel):
    packet: PacketView
    rejected: list[RowRejection]


class GenerationInput(PresalesModel):
    requirement: RequirementInput
    sources: list[SourceSnapshot]
    evidence: list[dict[str, str]]


class GeneratedDraft(PresalesModel):
    draft: ModelDraft
    usage: dict[str, int | None] | None = None
    returned_model: str | None = None
    provider_response_id: str | None = None
    provider_request_id: str | None = None
