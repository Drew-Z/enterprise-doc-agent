from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from enterprise_doc_core.agents.gateway import ChatModelGateway
from enterprise_doc_core.agents.schemas import (
    GroundedAnswer,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelRequest,
    GroundedRefusal,
    JsonSchemaNode,
)
from enterprise_doc_core.documents.retrieval import (
    Citation,
    RetrievalCandidate,
    decide_retrieval,
    validate_citations,
)


class GroundingValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def generate_grounded_answer(
    gateway: ChatModelGateway,
    request: GroundedModelRequest,
    *,
    tenant_id: UUID,
    document_version_id: UUID,
    min_score: float = 0.0,
    min_candidates: int = 1,
) -> GroundedAnswer | GroundedRefusal:
    candidates = _candidates(request.evidence)
    _authorize_request_evidence(
        request.evidence,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
    )
    decision = decide_retrieval(
        candidates,
        min_score=min_score,
        min_candidates=min_candidates,
    )
    if not decision.accepted:
        assert decision.refusal_reason is not None
        return GroundedRefusal(decision.refusal_reason)
    output = await gateway.generate(request)
    return validate_grounded_output(
        output,
        request=request,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
    )


def validate_grounded_output(
    output: GroundedModelOutput,
    *,
    request: GroundedModelRequest,
    tenant_id: UUID,
    document_version_id: UUID,
) -> GroundedAnswer | GroundedRefusal:
    if output.task_type is not request.task_type:
        raise GroundingValidationError(
            "output_task_mismatch",
            "The model output task type does not match the requested task.",
        )
    if output.is_refusal:
        refusal_reason = output.refusal_reason
        if refusal_reason is None:
            raise GroundingValidationError(
                "refusal_reason_required",
                "A model refusal must contain an allowed refusal reason.",
            )
        return GroundedRefusal(refusal_reason)
    if not output.citations:
        raise GroundingValidationError(
            "citation_required",
            "A non-refusal grounded answer must contain at least one citation.",
        )
    citation_ids = [citation.chunk_id for citation in output.citations]
    if len(citation_ids) != len(set(citation_ids)):
        raise GroundingValidationError(
            "duplicate_citation",
            "A grounded answer cannot cite the same chunk more than once.",
        )
    citations = tuple(
        Citation(
            chunk_id=citation.chunk_id,
            document_version_id=citation.document_version_id,
            excerpt=citation.excerpt,
        )
        for citation in output.citations
    )
    try:
        resolved = validate_citations(
            citations,
            _candidates(request.evidence),
            tenant_id=tenant_id,
            document_version_id=document_version_id,
        )
    except ValueError as error:
        code = str(error)
        raise GroundingValidationError(
            code,
            "The model citation did not pass the deterministic authorization gate.",
        ) from error
    if output.task_type.value == "structured_extraction":
        schema = request.extraction_schema
        fields = output.structured_fields
        if schema is None or fields is None:
            raise GroundingValidationError(
                "structured_output_missing",
                "Structured extraction output requires fields and a schema.",
            )
        _validate_json_value(fields, schema, path="$", error_code="structured_output_invalid")
    elif output.structured_fields is not None:
        raise GroundingValidationError(
            "structured_fields_not_allowed",
            "QA and summary outputs cannot contain structured fields.",
        )
    answer_text = output.answer_text
    if answer_text is None:
        raise GroundingValidationError(
            "answer_text_required",
            "A non-refusal grounded answer must contain answer text.",
        )
    return GroundedAnswer(
        task_type=output.task_type,
        answer_text=answer_text,
        structured_fields=output.structured_fields,
        citations=resolved,
        risk_hint=output.risk_hint,
        identity=output.identity,
        repaired=output.repaired,
    )


def _authorize_request_evidence(
    evidence: list[GroundedEvidence],
    *,
    tenant_id: UUID,
    document_version_id: UUID,
) -> None:
    for item in evidence:
        if item.tenant_id != tenant_id or item.document_version_id != document_version_id:
            raise GroundingValidationError(
                "evidence_not_authorized",
                "Evidence must belong to the requested tenant and document version.",
            )


def _candidates(evidence: list[GroundedEvidence]) -> tuple[RetrievalCandidate, ...]:
    return tuple(
        RetrievalCandidate(
            chunk_id=item.chunk_id,
            tenant_id=item.tenant_id,
            document_version_id=item.document_version_id,
            generation_id=item.generation_id,
            text=item.text,
            page_number=item.page_number,
            heading=item.heading,
            start_offset=item.start_offset,
            end_offset=item.end_offset,
            source_filename=item.source_filename,
            score=item.score,
        )
        for item in evidence
    )


def _validate_json_value(
    value: Any,
    schema: JsonSchemaNode,
    *,
    path: str,
    error_code: str,
) -> None:
    if schema.enum is not None and not any(
        type(value) is type(allowed) and value == allowed for allowed in schema.enum
    ):
        raise GroundingValidationError(error_code, f"{path} is not one of the allowed enum values")
    if schema.type == "object":
        if not isinstance(value, Mapping):
            raise GroundingValidationError(error_code, f"{path} must be an object")
        assert schema.properties is not None
        unknown = set(value) - set(schema.properties)
        if unknown:
            raise GroundingValidationError(error_code, f"{path} contains unsupported fields")
        missing = set(schema.required) - set(value)
        if missing:
            raise GroundingValidationError(error_code, f"{path} is missing required fields")
        for name, child in schema.properties.items():
            if name in value:
                _validate_json_value(
                    value[name], child, path=f"{path}.{name}", error_code=error_code
                )
        return
    if schema.type == "array":
        if not isinstance(value, list):
            raise GroundingValidationError(error_code, f"{path} must be an array")
        if schema.min_items is not None and len(value) < schema.min_items:
            raise GroundingValidationError(error_code, f"{path} has too few items")
        if schema.max_items is not None and len(value) > schema.max_items:
            raise GroundingValidationError(error_code, f"{path} has too many items")
        assert schema.items is not None
        for index, item in enumerate(value):
            _validate_json_value(item, schema.items, path=f"{path}[{index}]", error_code=error_code)
        return
    if schema.type == "string":
        if not isinstance(value, str):
            raise GroundingValidationError(error_code, f"{path} must be a string")
        if schema.min_length is not None and len(value) < schema.min_length:
            raise GroundingValidationError(error_code, f"{path} is too short")
        if schema.max_length is not None and len(value) > schema.max_length:
            raise GroundingValidationError(error_code, f"{path} is too long")
        return
    if schema.type == "boolean":
        if type(value) is not bool:
            raise GroundingValidationError(error_code, f"{path} must be a boolean")
        return
    if schema.type == "integer":
        if type(value) is not int:
            raise GroundingValidationError(error_code, f"{path} must be an integer")
        _validate_number_bounds(value, schema, path=path, error_code=error_code)
        return
    if schema.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GroundingValidationError(error_code, f"{path} must be a number")
        _validate_number_bounds(float(value), schema, path=path, error_code=error_code)
        return
    raise GroundingValidationError(error_code, f"{path} has an unsupported type")


def _validate_number_bounds(
    value: float,
    schema: JsonSchemaNode,
    *,
    path: str,
    error_code: str,
) -> None:
    if schema.minimum is not None and value < schema.minimum:
        raise GroundingValidationError(error_code, f"{path} is below minimum")
    if schema.maximum is not None and value > schema.maximum:
        raise GroundingValidationError(error_code, f"{path} is above maximum")


__all__ = [
    "GroundingValidationError",
    "generate_grounded_answer",
    "validate_grounded_output",
]
