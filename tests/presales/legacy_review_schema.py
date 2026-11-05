"""Frozen e553b323e4d4ae7ce7eea3778aa11429724d4edf review decoder for rollback compatibility.

Extracted unchanged with its dependency classes; do not evolve with current schemas.
"""

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


class SavedReview(ResponseText):
    revision: int
    note: str
    actor_id: UUID
    reviewed_at: datetime
