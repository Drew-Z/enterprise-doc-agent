from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.analysis.evidence import validate_candidate
from enterprise_doc_core.analysis.execution_schemas import (
    Candidate,
    EvidenceWindow,
    ToolDecision,
    parse_model_decision,
)
from enterprise_doc_core.analysis.schemas import AnalysisField
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.documents.structure import SourceLocator


def window():
    return EvidenceWindow(
        id="e1",
        document_version_id=uuid4(),
        generation_id=uuid4(),
        block_id="b1",
        block_hash="a" * 64,
        text="Setup costs USD 1200, excluding tax.",
        locator=SourceLocator(kind="txt", line=1),
        filename="offer.txt",
        coverage="complete",
    )


def test_citations_are_validated_against_the_server_evidence_set():
    evidence = window()
    field = AnalysisField(id="setup", name="Setup", question="What is the setup fee?")
    candidate = Candidate.model_validate(
        {
            "action": "final",
            "value": "USD 1200",
            "evidence_state": "supported",
            "citations": [{"evidence_id": "e1", "quote": "USD 1200"}],
            "conditions": [
                {
                    "text": "Tax excluded",
                    "citations": [{"evidence_id": "e1", "quote": "excluding tax"}],
                }
            ],
        }
    )
    result = validate_candidate(candidate, field, (evidence,))
    assert result.value == "USD 1200"
    assert result.evidence[0].document_version_id == evidence.document_version_id
    for bad in ("other_supplier", "common_requirements"):
        invalid = candidate.model_dump(mode="json")
        invalid["citations"][0]["evidence_id"] = bad
        with pytest.raises(AnalysisError, match="citation_invalid"):
            validate_candidate(Candidate.model_validate(invalid), field, (evidence,))


def test_unknown_is_not_false_and_unsupported_conditions_are_rejected():
    with pytest.raises(ValidationError):
        Candidate(action="abstain", evidence_state="insufficient", value=False, reason="Not found")
    with pytest.raises(ValidationError):
        Candidate(action="final", evidence_state="supported", value="1200")
    with pytest.raises(ValidationError):
        Candidate(action="tools", evidence_state="insufficient", reason="Search again")


def test_initial_tool_decision_is_bounded_and_terminal_schema_rejects_it():
    decision = parse_model_decision(
        {
            "action": "tools",
            "reason": "The initial windows do not contain the cancellation clause.",
            "tools": [
                {"tool": "search_sources", "query": "cancellation notice", "max_results": 4},
                {
                    "tool": "read_source",
                    "document_version_id": str(uuid4()),
                    "block_id": "known-block",
                },
            ],
        },
        allow_tools=True,
    )
    assert isinstance(decision, ToolDecision)
    assert len(decision.tools) == 2
    with pytest.raises(ValidationError):
        parse_model_decision(decision.model_dump(mode="json"), allow_tools=False)
    with pytest.raises(ValidationError):
        ToolDecision.model_validate(
            {
                "action": "tools",
                "reason": "too many",
                "tools": [{"tool": "search_sources", "query": str(index)} for index in range(4)],
            }
        )
