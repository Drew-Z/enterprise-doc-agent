# Chinese user-facing template uses full-width punctuation intentionally.
# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from enterprise_doc_core.documents.structure import DocumentBlockData, StructureIssue

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Label = Annotated[str, Field(min_length=1, max_length=160)]


class AnalysisModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        alias_generator=to_camel,
        str_strip_whitespace=True,
    )


class AnalysisField(AnalysisModel):
    id: Identifier
    name: Label
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    value_type: Literal[
        "text", "boolean", "decimal", "money", "date", "duration", "enum", "list"
    ] = "text"
    parameter_ids: tuple[Identifier, ...] = Field(default=(), max_length=8)
    upstream_field_ids: tuple[Identifier, ...] = Field(default=(), max_length=8)


class AnalysisParameter(AnalysisModel):
    id: Identifier
    name: Label
    value: Annotated[str, Field(min_length=1, max_length=2000)]
    unit: Annotated[str, Field(min_length=1, max_length=32)] | None = None


class ComparisonObject(AnalysisModel):
    id: Identifier
    name: Label
    source_version_ids: tuple[UUID, ...] = Field(default=(), max_length=10)


class AnalysisSpec(AnalysisModel):
    schema_version: Literal["analysis.spec.v1", "analysis.spec.v2"] = "analysis.spec.v2"
    common_requirements: tuple[UUID, ...] = Field(default=(), max_length=10)
    objects: tuple[ComparisonObject, ...] = Field(min_length=1, max_length=3)
    fields: tuple[AnalysisField, ...] = Field(min_length=1, max_length=10)
    parameters: tuple[AnalysisParameter, ...] = Field(default=(), max_length=8)

    def source_ids(self) -> tuple[UUID, ...]:
        return self.common_requirements + tuple(
            source for item in self.objects for source in item.source_version_ids
        )

    @model_validator(mode="after")
    def validate_scope(self) -> AnalysisSpec:
        sources = self.source_ids()
        if len(sources) > 10:
            raise ValueError("at most ten source files are supported")
        if len(sources) != len(set(sources)):
            raise ValueError("each version must belong to exactly one source group")
        for values in (self.objects, self.fields):
            if len({item.id for item in values}) != len(values):
                raise ValueError("object and field identifiers must be unique within each list")
        if len({item.id for item in self.parameters}) != len(self.parameters):
            raise ValueError("parameter identifiers must be unique")
        parameter_ids = {item.id for item in self.parameters}
        field_ids = {item.id for item in self.fields}
        if self.schema_version == "analysis.spec.v1" and (
            self.parameters
            or any(item.parameter_ids or item.upstream_field_ids for item in self.fields)
        ):
            raise ValueError("analysis.spec.v1 does not support dependencies")
        for field in self.fields:
            if len(field.parameter_ids) != len(set(field.parameter_ids)) or not set(
                field.parameter_ids
            ).issubset(parameter_ids):
                raise ValueError("field parameter dependencies must reference unique parameters")
            if (
                len(field.upstream_field_ids) != len(set(field.upstream_field_ids))
                or field.id in field.upstream_field_ids
                or not set(field.upstream_field_ids).issubset(field_ids)
            ):
                raise ValueError("field dependencies must reference other unique fields")
        pending = {item.id: set(item.upstream_field_ids) for item in self.fields}
        resolved: set[str] = set()
        while pending:
            ready = {key for key, dependencies in pending.items() if dependencies <= resolved}
            if not ready:
                raise ValueError("field dependencies must be acyclic")
            resolved.update(ready)
            pending = {key: value for key, value in pending.items() if key not in ready}
        return self


class ProjectView(AnalysisModel):
    id: UUID
    title: Label
    revision: int
    spec: AnalysisSpec
    created_at: datetime


class FrozenSource(AnalysisModel):
    document_version_id: UUID
    generation_id: UUID
    object_id: str | None
    filename: str
    source_sha256: str
    structure_sha256: str
    parser_version: Literal[2] = 2
    coverage: Literal["complete", "partial", "unknown"]
    block_count: int
    issues: tuple[StructureIssue, ...]


class SnapshotPayload(AnalysisModel):
    schema_version: Literal["analysis.sources.v1", "analysis.sources.v2"] = "analysis.sources.v2"
    title: str
    spec: AnalysisSpec
    sources: tuple[FrozenSource, ...]

    @model_validator(mode="after")
    def coherent_version(self) -> SnapshotPayload:
        expected = (
            "analysis.sources.v1"
            if self.spec.schema_version == "analysis.spec.v1"
            else "analysis.sources.v2"
        )
        if self.schema_version != expected:
            raise ValueError("source and analysis specification versions must match")
        return self


class SnapshotView(AnalysisModel):
    id: UUID
    project_id: UUID
    project_revision: int
    source_manifest_hash: str
    payload: SnapshotPayload
    created_at: datetime


class SourceBlocksView(AnalysisModel):
    source: FrozenSource
    blocks: tuple[DocumentBlockData, ...]
    next_offset: int | None


def canonical_hash(value: BaseModel | object) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    content = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def default_fields() -> tuple[AnalysisField, ...]:
    definitions = (
        ("scope", "服务范围", "包括哪些产品、服务和交付物？"),
        ("pricing_unit", "计费单位", "按什么数量、期间和单位计费？"),
        ("recurring_cost", "持续费用", "持续费用的金额、币种、数量和期间是什么？"),
        ("setup_fee", "一次性费用", "实施、安装或其他一次性费用及其适用条件是什么？"),
        ("tax_basis", "税费口径", "价格是否含税？税种、税率及其他费用是否明确？"),
        ("lead_time", "交付时间", "交付需要多久？从哪个事件开始计算？"),
        ("contract_term", "合同期限", "合同期限、续约和终止条件是什么？"),
        ("support", "售后支持", "支持范围、响应时间和服务时段是什么？"),
        ("exclusions", "排除与限制", "不包含哪些内容？有哪些前置条件和限制？"),
        ("payment_terms", "付款条件", "付款节点、比例及验收条件是什么？"),
    )
    return tuple(
        AnalysisField(id=key, name=name, question=question) for key, name, question in definitions
    )
