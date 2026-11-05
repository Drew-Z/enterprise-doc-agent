from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from enterprise_doc_core.analysis.schemas import AnalysisModel, Identifier, SnapshotView
from enterprise_doc_core.documents.structure import SourceLocator

Text = Annotated[str, Field(min_length=1, max_length=2000)]
DecimalText = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?$")]
UnitStatus = Literal[
    "queued", "running", "waiting_input", "succeeded", "failed", "cancelled", "expired"
]
RunStatus = Literal[
    "queued", "running", "waiting_input", "completed", "partial", "failed", "cancelled", "expired"
]


class ExecutionPolicy(AnalysisModel):
    schema_version: Literal["analysis.execution.v1", "analysis.execution.v2"] = (
        "analysis.execution.v1"
    )
    prompt_version: Literal["analysis.fixed.v1", "analysis.agent.v1"] = "analysis.fixed.v1"
    retrieval_version: Literal["analysis.blocks.v1"] = "analysis.blocks.v1"
    tool_version: Literal["analysis.tools.v1"] = "analysis.tools.v1"
    execution_mode: Literal["fixed", "agent"] = "fixed"
    route_id: str
    model: str
    model_revision: str | None = None
    channel_hash: str
    streaming: bool
    reasoning_effort: str | None = None
    timeout_seconds: float = Field(gt=0, le=300)
    unit_deadline_seconds: float = Field(gt=0, le=600)
    run_deadline_seconds: float = Field(gt=0, le=21600)
    max_input_bytes: int = Field(ge=1024, le=65536)
    max_output_bytes: int = Field(ge=1024, le=262144)
    max_dispatches: Literal[2] = 2
    max_tool_rounds: Literal[0, 1] = 0

    @model_validator(mode="after")
    def coherent_mode(self) -> ExecutionPolicy:
        expected = (
            ("analysis.execution.v1", "analysis.fixed.v1", 0)
            if self.execution_mode == "fixed"
            else ("analysis.execution.v2", "analysis.agent.v1", 1)
        )
        if (self.schema_version, self.prompt_version, self.max_tool_rounds) != expected:
            raise ValueError("execution policy mode and versions do not match")
        return self


class Money(AnalysisModel):
    amount: DecimalText
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Duration(AnalysisModel):
    amount: DecimalText
    unit: Literal["hour", "day", "week", "month", "year"]
    starts_when: Text


Value = StrictStr | StrictBool | Money | Duration | tuple[Text, ...] | None


class CitationInput(AnalysisModel):
    evidence_id: Annotated[str, Field(min_length=1, max_length=80)]
    quote: Annotated[str, Field(min_length=1, max_length=1000)]


class Condition(AnalysisModel):
    text: Text
    citations: tuple[CitationInput, ...] = Field(min_length=1, max_length=8)


class Candidate(AnalysisModel):
    action: Literal["final", "abstain", "need_input"]
    value: Value = None
    evidence_state: Literal["supported", "conflicting", "insufficient", "unreadable"]
    citations: tuple[CitationInput, ...] = Field(default=(), max_length=8)
    conditions: tuple[Condition, ...] = Field(default=(), max_length=8)
    reason: Annotated[str, Field(max_length=2000)] = ""
    missing_parameters: tuple[Text, ...] = Field(default=(), max_length=8)
    calculation_id: Annotated[str, Field(min_length=1, max_length=80)] | None = None

    @model_validator(mode="after")
    def coherent(self) -> Candidate:
        value_origin = getattr(self, "value_origin", "document_evidence")
        if isinstance(self.value, str) and (not self.value or len(self.value) > 4000):
            raise ValueError("invalid value length")
        if isinstance(self.value, tuple) and not 1 <= len(self.value) <= 30:
            raise ValueError("invalid list length")
        if self.evidence_state == "supported":
            if self.action != "final" or self.value is None or not self.citations:
                raise ValueError("supported values require evidence")
        elif value_origin == "user_provided":
            if self.action == "final" and self.value is None:
                raise ValueError("final user-provided values cannot be null")
            if self.action != "final" and self.value is not None:
                raise ValueError("unresolved user-provided values must be null")
        elif self.value is not None or not self.reason:
            raise ValueError("unresolved values must be null with a reason")
        if self.evidence_state == "conflicting" and len(self.citations) < 2:
            raise ValueError("conflicts require both sides")
        if self.action == "need_input" and not self.missing_parameters:
            raise ValueError("input requests require missing parameters")
        if self.action != "final" and self.calculation_id is not None:
            raise ValueError("only final values can reference a calculation")
        return self


class SearchSourcesRequest(AnalysisModel):
    tool: Literal["search_sources"]
    query: Annotated[str, Field(min_length=1, max_length=500)]
    max_results: int = Field(default=8, ge=1, le=8)


class ReadSourceRequest(AnalysisModel):
    tool: Literal["read_source"]
    document_version_id: UUID
    block_id: Annotated[str, Field(min_length=1, max_length=128)]
    before: int = Field(default=0, ge=0, le=3)
    after: int = Field(default=0, ge=0, le=3)


class CalculationOperand(AnalysisModel):
    name: Identifier
    value: DecimalText | None = None
    parameter: Identifier | None = None
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    evidence_id: Annotated[str, Field(min_length=1, max_length=80)] | None = None

    @model_validator(mode="after")
    def one_source(self) -> CalculationOperand:
        if (self.value is None) == (self.parameter is None):
            raise ValueError("an operand needs exactly one value source")
        if self.value is not None and self.evidence_id is None:
            raise ValueError("literal operands need an evidence reference")
        if self.parameter is not None and self.evidence_id is not None:
            raise ValueError("input operands cannot claim document evidence")
        return self


class CalculateRequest(AnalysisModel):
    tool: Literal["calculate"]
    operation: Literal["sum", "product", "unit_conversion", "explicit_period_total"]
    operands: tuple[CalculationOperand, ...] = Field(min_length=1, max_length=12)
    result_unit: Annotated[str, Field(min_length=1, max_length=32)]
    rounding: Literal["half_even", "half_up", "down"] = "half_even"
    decimal_places: int = Field(default=2, ge=0, le=8)


ToolRequest = Annotated[
    SearchSourcesRequest | ReadSourceRequest | CalculateRequest,
    Field(discriminator="tool"),
]


class ToolDecision(AnalysisModel):
    action: Literal["tools"]
    reason: Text
    tools: tuple[ToolRequest, ...] = Field(min_length=1, max_length=3)


ModelDecision = Candidate | ToolDecision


def parse_model_decision(payload: object, *, allow_tools: bool) -> ModelDecision:
    if isinstance(payload, dict) and payload.get("action") == "tools":
        if allow_tools:
            return ToolDecision.model_validate(payload)
        # Preserve Pydantic's structured protocol error for callers and tests.
        return Candidate.model_validate(payload)
    return Candidate.model_validate(payload)


class EvidenceWindow(AnalysisModel):
    id: str
    document_version_id: UUID
    generation_id: UUID
    block_id: str
    block_hash: str
    text: Annotated[str, Field(max_length=2000)]
    locator: SourceLocator
    filename: str
    coverage: Literal["complete", "partial", "unknown"]
    formula_unverified: bool = False


class EvidenceSet(AnalysisModel):
    windows: tuple[EvidenceWindow, ...] = Field(max_length=8)
    searched_versions: tuple[UUID, ...]
    searched_blocks: int
    returned_blocks: int
    coverage: Literal["complete", "partial", "unknown"]
    # Search coverage is distinct from parser coverage; bounded windows are not a full read.
    exhaustive_read: Literal[False] = False


class ResolvedEvidence(AnalysisModel):
    evidence_id: str
    document_version_id: UUID
    generation_id: UUID
    block_id: str
    block_hash: str
    quote: str
    quote_hash: str
    locator: SourceLocator
    filename: str


class ResolvedCalculationOperand(AnalysisModel):
    name: Identifier
    value: DecimalText
    unit: str
    source: str


class CalculationReceipt(AnalysisModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    formula_version: Literal["analysis.calculation.v1"] = "analysis.calculation.v1"
    operation: Literal["sum", "product", "unit_conversion", "explicit_period_total"]
    expression: Annotated[str, Field(min_length=1, max_length=1000)]
    operands: tuple[ResolvedCalculationOperand, ...]
    result: DecimalText
    result_unit: str
    rounding: Literal["half_even", "half_up", "down"]
    decimal_places: int = Field(ge=0, le=8)


class CellResult(Candidate):
    evidence: tuple[ResolvedEvidence, ...]
    calculations: tuple[CalculationReceipt, ...] = ()
    value_origin: Literal["document_evidence", "user_provided"] = "document_evidence"
    comparison_state: Literal["not_comparable"] = "not_comparable"
    review_state: Literal["unreviewed", "approved", "changes_requested"] = "unreviewed"


class InputParameter(AnalysisModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    unit: Annotated[str, Field(min_length=1, max_length=32)] | None = None


class InputRequestView(AnalysisModel):
    id: UUID
    unit_id: UUID
    revision: int = Field(ge=1)
    parameters: tuple[InputParameter, ...] = Field(min_length=1, max_length=8)
    state: Literal["open", "submitted", "cancelled"]
    can_resume: bool


class InputSubmission(AnalysisModel):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    values: dict[str, Annotated[str, Field(min_length=1, max_length=2000)]]

    @model_validator(mode="after")
    def bounded_values(self) -> InputSubmission:
        if not 1 <= len(self.values) <= 8:
            raise ValueError("input submissions need one to eight values")
        return self


class ReviewView(AnalysisModel):
    id: UUID
    cell_revision_id: UUID
    revision: int = Field(ge=1)
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    decision: Literal["approved", "changes_requested", "rejected"]
    reason: Text
    actor_id: UUID
    created_at: datetime


class UnitView(AnalysisModel):
    id: UUID
    object_id: Identifier
    field_id: Identifier
    status: UnitStatus
    dispatches: int
    error_code: str | None
    result: CellResult | None
    search: EvidenceSet | None
    input_request: InputRequestView | None = None
    current_revision: int | None = Field(default=None, ge=1)
    current_revision_id: UUID | None = None
    current_content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    author_type: Literal["model", "user"] | None = None
    review: ReviewView | None = None
    freshness: Literal["current", "stale"] = "current"
    invalidation_reasons: tuple[str, ...] = ()
    reused_from_unit_id: UUID | None = None


class RunView(AnalysisModel):
    id: UUID
    project_id: UUID
    snapshot_id: UUID
    status: RunStatus
    total_units: int
    finished_units: int
    dispatches: int
    max_dispatches: int
    execution_mode: Literal["fixed", "agent"]
    created_at: datetime
    deadline_at: datetime
    cancel_requested: bool
    base_run_id: UUID | None = None
    reused_units: int = Field(default=0, ge=0)
    affected_units: int = Field(default=0, ge=0)


class MatrixView(AnalysisModel):
    run: RunView
    snapshot: SnapshotView
    units: tuple[UnitView, ...]


class EventView(AnalysisModel):
    sequence: int
    event_type: str
    unit_id: UUID | None
    code: str | None
