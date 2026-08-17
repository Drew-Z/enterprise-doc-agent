from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.documents.retrieval import RefusalReason, ResolvedCitation


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class BehaviorVersions(_StrictModel):
    graph_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    tool_schema_version: str = Field(min_length=1, max_length=64)


class GroundedEvidence(_StrictModel):
    chunk_id: UUID
    tenant_id: UUID
    document_version_id: UUID
    generation_id: UUID
    text: str = Field(min_length=1, max_length=200_000)
    rank: int = Field(ge=1, le=500)
    score: float
    page_number: int | None = Field(default=None, ge=1)
    heading: str | None = Field(default=None, max_length=500)
    source_filename: str | None = Field(default=None, max_length=255)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> GroundedEvidence:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return self


SchemaType = Literal[
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
]


class JsonSchemaNode(_StrictModel):
    """The intentionally small JSON Schema dialect accepted by extraction tasks."""

    type: SchemaType
    properties: dict[str, JsonSchemaNode] | None = None
    required: list[str] = Field(default_factory=list)
    additional_properties: Literal[False] = Field(
        default=False,
        alias="additionalProperties",
    )
    items: JsonSchemaNode | None = None
    enum: list[JsonValue] | None = None
    min_length: int | None = Field(default=None, alias="minLength", ge=0)
    max_length: int | None = Field(default=None, alias="maxLength", ge=0)
    minimum: float | None = None
    maximum: float | None = None
    min_items: int | None = Field(default=None, alias="minItems", ge=0)
    max_items: int | None = Field(default=None, alias="maxItems", ge=0)

    @model_validator(mode="after")
    def validate_supported_shape(self) -> JsonSchemaNode:
        if len(set(self.required)) != len(self.required):
            raise ValueError("required fields must be unique")
        if self.min_length is not None and self.max_length is not None:
            if self.min_length > self.max_length:
                raise ValueError("minLength must not exceed maxLength")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum must not exceed maximum")
        for bound_name, bound in (("minimum", self.minimum), ("maximum", self.maximum)):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"{bound_name} must be finite")
        if self.type == "integer" and self.minimum is not None and self.maximum is not None:
            if math.ceil(self.minimum) > math.floor(self.maximum):
                raise ValueError("integer minimum and maximum must contain an integer")
        if self.min_items is not None and self.max_items is not None:
            if self.min_items > self.max_items:
                raise ValueError("minItems must not exceed maxItems")

        if self.type == "object":
            if self.properties is None:
                raise ValueError("object schemas require properties")
            if self.items is not None:
                raise ValueError("object schemas cannot define items")
            unknown_required = set(self.required) - set(self.properties)
            if unknown_required:
                raise ValueError("required fields must be declared in properties")
            if self.min_length is not None or self.max_length is not None:
                raise ValueError("object schemas cannot define string length bounds")
            if self.min_items is not None or self.max_items is not None:
                raise ValueError("object schemas cannot define array length bounds")
        elif self.type == "array":
            if self.items is None:
                raise ValueError("array schemas require items")
            if self.properties is not None or self.required:
                raise ValueError("array schemas cannot define object fields")
            if self.min_length is not None or self.max_length is not None:
                raise ValueError("array schemas cannot define string length bounds")
        else:
            if self.properties is not None or self.items is not None or self.required:
                raise ValueError("scalar schemas cannot define nested fields")
            if self.min_items is not None or self.max_items is not None:
                raise ValueError("scalar schemas cannot define array length bounds")
            if self.type != "string" and (
                self.min_length is not None or self.max_length is not None
            ):
                raise ValueError("only string schemas can define length bounds")
        if self.enum is not None:
            for enum_value in self.enum:
                if not _enum_value_matches_type(enum_value, self):
                    raise ValueError("enum values must match the declared schema type")
        return self


def _enum_value_matches_type(value: JsonValue, schema: JsonSchemaNode) -> bool:
    if schema.type == "object":
        if not isinstance(value, Mapping):
            return False
        assert schema.properties is not None
        if set(value) - set(schema.properties):
            return False
        if set(schema.required) - set(value):
            return False
        return all(
            name not in value or _enum_value_matches_type(value[name], child)
            for name, child in schema.properties.items()
        )
    if schema.type == "array":
        if not isinstance(value, list):
            return False
        if schema.min_items is not None and len(value) < schema.min_items:
            return False
        if schema.max_items is not None and len(value) > schema.max_items:
            return False
        assert schema.items is not None
        return all(_enum_value_matches_type(item, schema.items) for item in value)
    if schema.type == "string":
        return (
            isinstance(value, str)
            and (schema.min_length is None or len(value) >= schema.min_length)
            and (schema.max_length is None or len(value) <= schema.max_length)
        )
    if schema.type == "boolean":
        return type(value) is bool
    if schema.type == "integer":
        return (
            type(value) is int
            and (schema.minimum is None or value >= schema.minimum)
            and (schema.maximum is None or value <= schema.maximum)
        )
    if schema.type == "number":
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and (schema.minimum is None or value >= schema.minimum)
            and (schema.maximum is None or value <= schema.maximum)
        )


class StructuredExtractionSchema(JsonSchemaNode):
    type: Literal["object"] = "object"


class GroundedModelRequest(_StrictModel):
    task_type: AgentRunTaskType
    user_input: str = Field(min_length=1, max_length=20_000)
    evidence: list[GroundedEvidence] = Field(max_length=50)
    extraction_schema: StructuredExtractionSchema | None = None
    behavior_versions: BehaviorVersions

    @field_validator("user_input")
    @classmethod
    def normalize_user_input(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_input must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_task_schema(self) -> GroundedModelRequest:
        requires_schema = self.task_type is AgentRunTaskType.STRUCTURED_EXTRACTION
        if requires_schema and self.extraction_schema is None:
            raise ValueError("structured extraction requires extraction_schema")
        if not requires_schema and self.extraction_schema is not None:
            raise ValueError("extraction_schema is only valid for structured extraction")
        return self


class CitationProposal(_StrictModel):
    chunk_id: UUID
    document_version_id: UUID
    excerpt: str = Field(min_length=1, max_length=500)


class RiskHint(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _TaskOutput(_StrictModel):
    outcome: Literal["answer"]
    answer_text: str = Field(min_length=1, max_length=100_000)
    citations: list[CitationProposal] = Field(max_length=50)
    risk_hint: RiskHint | None = None
    refusal_reason: None


class QuestionAnswerModelOutput(_TaskOutput):
    task_type: Literal[AgentRunTaskType.QUESTION_ANSWER] = AgentRunTaskType.QUESTION_ANSWER
    structured_fields: None = None


class SummaryModelOutput(_TaskOutput):
    task_type: Literal[AgentRunTaskType.SUMMARY] = AgentRunTaskType.SUMMARY
    structured_fields: None = None


class StructuredExtractionModelOutput(_TaskOutput):
    task_type: Literal[AgentRunTaskType.STRUCTURED_EXTRACTION] = (
        AgentRunTaskType.STRUCTURED_EXTRACTION
    )
    structured_fields: dict[str, JsonValue]


type GroundedAnswerModelPayload = Annotated[
    QuestionAnswerModelOutput | SummaryModelOutput | StructuredExtractionModelOutput,
    Field(discriminator="task_type"),
]


class ModelRefusalOutput(_StrictModel):
    outcome: Literal["refusal"]
    task_type: AgentRunTaskType
    refusal_reason: Literal["insufficient_evidence"]
    answer_text: None
    structured_fields: None
    citations: list[CitationProposal] = Field(max_length=0)
    risk_hint: None


type GroundedModelPayload = GroundedAnswerModelPayload | ModelRefusalOutput


class ModelIdentity(_StrictModel):
    provider: str = Field(min_length=1, max_length=32)
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str | None = Field(default=None, max_length=100)
    model_revision: str | None = Field(default=None, max_length=128)


class ModelCallTelemetry(_StrictModel):
    provider_request_count: int = Field(default=0, ge=0)
    usage_request_count: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    repair_request_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    breaker_state: Literal["closed", "open", "half_open"] | None = None

    @model_validator(mode="after")
    def validate_request_counts(self) -> ModelCallTelemetry:
        if self.usage_request_count > self.provider_request_count:
            raise ValueError("usage_request_count cannot exceed provider_request_count")
        if self.repair_request_count > self.provider_request_count:
            raise ValueError("repair_request_count cannot exceed provider_request_count")
        return self


@dataclass(frozen=True, slots=True)
class GroundedModelOutput:
    payload: GroundedModelPayload
    identity: ModelIdentity
    repaired: bool = False
    telemetry: ModelCallTelemetry = field(default_factory=ModelCallTelemetry)

    @property
    def task_type(self) -> AgentRunTaskType:
        return self.payload.task_type

    @property
    def is_refusal(self) -> bool:
        return isinstance(self.payload, ModelRefusalOutput)

    @property
    def refusal_reason(self) -> RefusalReason | None:
        if isinstance(self.payload, ModelRefusalOutput):
            return RefusalReason(self.payload.refusal_reason)
        return None

    @property
    def answer_text(self) -> str | None:
        return self.payload.answer_text

    @property
    def structured_fields(self) -> dict[str, JsonValue] | None:
        return self.payload.structured_fields

    @property
    def citations(self) -> tuple[CitationProposal, ...]:
        return tuple(self.payload.citations)

    @property
    def risk_hint(self) -> RiskHint | None:
        return self.payload.risk_hint


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    task_type: AgentRunTaskType
    answer_text: str
    structured_fields: dict[str, JsonValue] | None
    citations: tuple[ResolvedCitation, ...]
    risk_hint: RiskHint | None
    identity: ModelIdentity
    repaired: bool


@dataclass(frozen=True, slots=True)
class GroundedRefusal:
    reason: RefusalReason


__all__ = [
    "BehaviorVersions",
    "CitationProposal",
    "GroundedAnswer",
    "GroundedAnswerModelPayload",
    "GroundedEvidence",
    "GroundedModelOutput",
    "GroundedModelPayload",
    "GroundedModelRequest",
    "GroundedRefusal",
    "JsonSchemaNode",
    "ModelCallTelemetry",
    "ModelIdentity",
    "ModelRefusalOutput",
    "QuestionAnswerModelOutput",
    "RiskHint",
    "StructuredExtractionModelOutput",
    "StructuredExtractionSchema",
    "SummaryModelOutput",
]
