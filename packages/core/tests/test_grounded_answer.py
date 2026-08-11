from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.agents import (
    AgentRunTaskType,
    BehaviorVersions,
    CitationProposal,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelRequest,
    GroundedRefusal,
    GroundingValidationError,
    ModelIdentity,
    ModelRefusalOutput,
    QuestionAnswerModelOutput,
    StructuredExtractionModelOutput,
    StructuredExtractionSchema,
    generate_grounded_answer,
    validate_grounded_output,
)
from enterprise_doc_core.documents import RefusalReason

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT = UUID("00000000-0000-0000-0000-000000000002")
VERSION = UUID("00000000-0000-0000-0000-000000000011")
OTHER_VERSION = UUID("00000000-0000-0000-0000-000000000012")
GENERATION = UUID("00000000-0000-0000-0000-000000000021")
CHUNK = UUID("00000000-0000-0000-0000-000000000031")


class FixedGateway:
    def __init__(self, output: GroundedModelOutput) -> None:
        self.output = output
        self.calls = 0

    async def generate(self, _: GroundedModelRequest) -> GroundedModelOutput:
        self.calls += 1
        return self.output


class ExplodingGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, _: GroundedModelRequest) -> GroundedModelOutput:
        self.calls += 1
        raise AssertionError("model gateway must not be called")


def _evidence(
    *,
    tenant_id: UUID = TENANT,
    version_id: UUID = VERSION,
    chunk_id: UUID = CHUNK,
    score: float = 0.9,
) -> GroundedEvidence:
    text = "Payment is due within 30 days after acceptance."
    return GroundedEvidence(
        chunk_id=chunk_id,
        tenant_id=tenant_id,
        document_version_id=version_id,
        generation_id=GENERATION,
        text=text,
        rank=1,
        score=score,
        page_number=1,
        heading="Payment",
        source_filename="contract.txt",
        start_offset=100,
        end_offset=100 + len(text),
    )


def _request(
    *,
    evidence: list[GroundedEvidence] | None = None,
    task_type: AgentRunTaskType = AgentRunTaskType.QUESTION_ANSWER,
    extraction_schema: StructuredExtractionSchema | None = None,
) -> GroundedModelRequest:
    return GroundedModelRequest(
        task_type=task_type,
        user_input="What are the payment terms?",
        evidence=evidence if evidence is not None else [_evidence()],
        extraction_schema=extraction_schema,
        behavior_versions=BehaviorVersions(
            graph_version="m4.v1",
            prompt_version="m4.v1",
            tool_schema_version="m4.v1",
        ),
    )


def _qa_output(
    *,
    chunk_id: UUID = CHUNK,
    version_id: UUID = VERSION,
    excerpt: str = "Payment is due within 30 days",
    duplicate: bool = False,
) -> GroundedModelOutput:
    citation = CitationProposal(
        chunk_id=chunk_id,
        document_version_id=version_id,
        excerpt=excerpt,
    )
    citations = [citation, citation] if duplicate else [citation]
    return GroundedModelOutput(
        payload=QuestionAnswerModelOutput(
            outcome="answer",
            answer_text="Payment is due within 30 days after acceptance.",
            citations=citations,
            refusal_reason=None,
        ),
        identity=ModelIdentity(
            provider="deterministic",
            model_name="deterministic-grounded",
            model_version="m4.v1",
        ),
    )


def _refusal_output() -> GroundedModelOutput:
    return GroundedModelOutput(
        payload=ModelRefusalOutput(
            outcome="refusal",
            task_type=AgentRunTaskType.QUESTION_ANSWER,
            refusal_reason="insufficient_evidence",
            answer_text=None,
            structured_fields=None,
            citations=[],
            risk_hint=None,
        ),
        identity=ModelIdentity(
            provider="openai_compatible",
            model_name="test-model",
            model_version="2026-08",
        ),
    )


async def test_valid_grounded_answer_resolves_existing_citation_metadata() -> None:
    gateway = FixedGateway(_qa_output())
    result = await generate_grounded_answer(
        gateway,
        _request(),
        tenant_id=TENANT,
        document_version_id=VERSION,
    )

    assert gateway.calls == 1
    assert not isinstance(result, GroundedRefusal)
    assert result.citations[0].source_filename == "contract.txt"
    assert result.citations[0].page_number == 1
    assert result.identity.model_name == "deterministic-grounded"


async def test_model_refusal_is_valid_after_retrieval_accepts_candidates() -> None:
    gateway = FixedGateway(_refusal_output())

    result = await generate_grounded_answer(
        gateway,
        _request(),
        tenant_id=TENANT,
        document_version_id=VERSION,
    )

    assert result == GroundedRefusal(RefusalReason.INSUFFICIENT_EVIDENCE)
    assert gateway.calls == 1


@pytest.mark.parametrize(
    ("evidence", "min_score", "min_candidates", "reason"),
    [
        ([], 0.0, 1, RefusalReason.EMPTY_EVIDENCE),
        ([_evidence()], 0.0, 2, RefusalReason.INSUFFICIENT_EVIDENCE),
        ([_evidence(score=0.1)], 0.2, 1, RefusalReason.LOW_RELEVANCE),
    ],
)
async def test_retrieval_refusal_never_calls_model(
    evidence: list[GroundedEvidence],
    min_score: float,
    min_candidates: int,
    reason: RefusalReason,
) -> None:
    gateway = ExplodingGateway()
    result = await generate_grounded_answer(
        gateway,
        _request(evidence=evidence),
        tenant_id=TENANT,
        document_version_id=VERSION,
        min_score=min_score,
        min_candidates=min_candidates,
    )

    assert result == GroundedRefusal(reason)
    assert gateway.calls == 0


async def test_cross_tenant_evidence_is_rejected_before_model() -> None:
    gateway = ExplodingGateway()
    with pytest.raises(GroundingValidationError) as error:
        await generate_grounded_answer(
            gateway,
            _request(evidence=[_evidence(tenant_id=OTHER_TENANT)]),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )
    assert error.value.code == "evidence_not_authorized"
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("output", "expected_code", "expected_diagnostic"),
    [
        (
            _qa_output(version_id=OTHER_VERSION),
            RefusalReason.CITATION_WRONG_VERSION.value,
            "grounding.citation_wrong_version",
        ),
        (
            _qa_output(chunk_id=uuid4()),
            RefusalReason.CITATION_NOT_IN_CANDIDATES.value,
            "grounding.citation_chunk_not_in_candidates",
        ),
        (
            _qa_output(excerpt="Payment is due whenever the model says so"),
            RefusalReason.CITATION_NOT_IN_CANDIDATES.value,
            "grounding.citation_excerpt_not_verbatim",
        ),
        (_qa_output(duplicate=True), "duplicate_citation", None),
    ],
)
def test_citation_failures_are_deterministic_and_not_model_repairable(
    output: GroundedModelOutput,
    expected_code: str,
    expected_diagnostic: str | None,
) -> None:
    with pytest.raises(GroundingValidationError) as error:
        validate_grounded_output(
            output,
            request=_request(),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )
    assert error.value.code == expected_code
    assert error.value.diagnostic_code == expected_diagnostic


def test_cross_tenant_candidate_uses_existing_citation_authorization_gate() -> None:
    with pytest.raises(GroundingValidationError) as error:
        validate_grounded_output(
            _qa_output(),
            request=_request(evidence=[_evidence(tenant_id=OTHER_TENANT)]),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )
    assert error.value.code == RefusalReason.CITATION_NOT_AUTHORIZED.value
    assert error.value.diagnostic_code == "grounding.citation_not_authorized"


def test_non_refusal_answer_requires_at_least_one_citation() -> None:
    output = GroundedModelOutput(
        payload=QuestionAnswerModelOutput(
            outcome="answer",
            answer_text="Unsupported answer",
            citations=[],
            refusal_reason=None,
        ),
        identity=ModelIdentity(provider="deterministic", model_name="deterministic-grounded"),
    )
    with pytest.raises(GroundingValidationError) as error:
        validate_grounded_output(
            output,
            request=_request(),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )
    assert error.value.code == "citation_required"


def test_structured_extraction_accepts_only_the_declared_closed_schema() -> None:
    schema = StructuredExtractionSchema.model_validate(
        {
            "type": "object",
            "properties": {
                "payment_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "currency": {"type": "string", "enum": ["CNY", "USD"]},
                "milestones": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 3,
                },
            },
            "required": ["payment_days", "currency"],
            "additionalProperties": False,
        }
    )
    request = _request(
        task_type=AgentRunTaskType.STRUCTURED_EXTRACTION,
        extraction_schema=schema,
    )
    valid = GroundedModelOutput(
        payload=StructuredExtractionModelOutput(
            outcome="answer",
            answer_text="Structured extraction completed.",
            structured_fields={
                "payment_days": 30,
                "currency": "CNY",
                "milestones": ["acceptance"],
            },
            citations=_qa_output().payload.citations,
            refusal_reason=None,
        ),
        identity=ModelIdentity(provider="deterministic", model_name="deterministic-grounded"),
    )
    result = validate_grounded_output(
        valid,
        request=request,
        tenant_id=TENANT,
        document_version_id=VERSION,
    )
    assert result.structured_fields == valid.structured_fields

    invalid_fields = [
        {"payment_days": "30", "currency": "CNY"},
        {"payment_days": 30},
        {"payment_days": 30, "currency": "EUR"},
        {"payment_days": 30, "currency": "CNY", "undeclared": True},
        {"payment_days": 30, "currency": "CNY", "milestones": ["a", "b", "c", "d"]},
    ]
    for fields in invalid_fields:
        output = GroundedModelOutput(
            payload=StructuredExtractionModelOutput(
                outcome="answer",
                answer_text="Structured extraction completed.",
                structured_fields=fields,
                citations=_qa_output().payload.citations,
                refusal_reason=None,
            ),
            identity=valid.identity,
        )
        with pytest.raises(GroundingValidationError) as error:
            validate_grounded_output(
                output,
                request=request,
                tenant_id=TENANT,
                document_version_id=VERSION,
            )
        assert error.value.code == "structured_output_invalid"


def test_structured_schema_rejects_missing_required_declarations_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StructuredExtractionSchema.model_validate(
            {
                "type": "object",
                "properties": {"known": {"type": "string"}},
                "required": ["unknown"],
            }
        )
    with pytest.raises(ValidationError):
        StructuredExtractionSchema.model_validate(
            {
                "type": "object",
                "properties": {"known": {"type": "string", "pattern": ".*"}},
            }
        )
