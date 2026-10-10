from __future__ import annotations

import json
from uuid import uuid4

import pytest

from enterprise_doc_core.presales.evidence_selection import resolve_evidence_selection
from enterprise_doc_core.presales.proposition_selection import (
    resolve_proposition_selection,
)
from enterprise_doc_core.presales.schemas import CitationInput


def test_business_propositions_survive_public_condition_projection():
    citation = CitationInput(
        chunk_id=uuid4(),
        document_version_id=uuid4(),
        excerpt="已采购归档。配置尚未完成。验收状态未登记。",
    )
    prerequisites = [
        {
            "proposition": proposition,
            "uncertainty": "missing" if quote is None else "none",
            "positive": [{"citationId": "source", "text": quote}] if state == "met" else [],
            "negative": [{"citationId": "source", "text": quote}] if state == "unmet" else [],
            "citations": [{"citationId": "source"}],
        }
        for proposition, state, quote in [
            ("归档模块已采购。", "met", "已采购归档。"),
            ("归档保留策略已完成配置。", "unmet", "配置尚未完成。"),
            ("归档恢复验收已通过。", "unknown", None),
        ]
    ]
    result = resolve_proposition_selection(
        json.dumps(
            {
                "prerequisites": prerequisites,
                "status": "conditional",
                "answer": "已采购归档\uff0c需配置保留策略并确认恢复验收。",
                "missingInformation": ["请确认恢复验收是否已通过。"],
            }
        ),
        {"source": citation},
    )
    assert [p.model_dump(by_alias=True) for p in result.prerequisites] == [
        {"condition": "核验事项\uff1a归档模块已采购。", "state": "met", "citationIndexes": [0]},
        {
            "condition": "核验事项\uff1a归档保留策略已完成配置。",
            "state": "unmet",
            "citationIndexes": [0],
        },
        {
            "condition": "核验事项\uff1a归档恢复验收已通过。",
            "state": "unknown",
            "citationIndexes": [0],
        },
    ]
    assert result.conditions == [
        "核验事项\uff1a归档保留策略已完成配置。",
        "核验事项\uff1a归档恢复验收已通过。",
    ]
    assert result.citations == [citation]


@pytest.mark.parametrize("proposition", ["Acceptance has completed.", "验" * 996, " "])
def test_prefix_does_not_hide_invalid_proposition(proposition):
    citation = CitationInput(chunk_id=uuid4(), document_version_id=uuid4(), excerpt="验收未登记。")
    content = json.dumps(
        {
            "status": "conditional",
            "answer": "需确认验收。",
            "prerequisites": [
                {
                    "proposition": proposition,
                    "uncertainty": "missing",
                    "positive": [],
                    "negative": [],
                    "citations": [{"citationId": "source"}],
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        resolve_proposition_selection(content, {"source": citation})


def test_legacy_condition_is_preserved_only_by_legacy_decoder():
    citation = CitationInput(chunk_id=uuid4(), document_version_id=uuid4(), excerpt="验收未登记。")
    content = json.dumps(
        {
            "status": "conditional",
            "answer": "需确认验收。",
            "prerequisites": [
                {
                    "proposition": "验收已通过。",
                    "uncertainty": "missing",
                    "positive": [],
                    "negative": [],
                    "condition": "旧协议独立的验收问题。",
                    "citations": [{"citationId": "source"}],
                }
            ],
        }
    )
    old = resolve_evidence_selection(content, {"source": citation})
    assert old.conditions == ["旧协议独立的验收问题。"]
    with pytest.raises(ValueError, match="condition"):
        resolve_proposition_selection(content, {"source": citation})
