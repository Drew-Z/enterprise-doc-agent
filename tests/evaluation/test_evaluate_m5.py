from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import scripts.evaluate_m5 as evaluate_m5

from enterprise_doc_core.evaluation import verify_report_payload


def _agent_report() -> dict[str, object]:
    return {
        "passed": True,
        "dataset_version": "agent-v1",
        "summary": {"passed": 1, "failed": 0, "total": 1},
        "cases": [{"case_id": "safe", "passed": True, "observed": {}}],
    }


@pytest.mark.asyncio
async def test_skip_rag_is_blocked_not_a_unified_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rag_dataset = tmp_path / "rag.json"
    agent_dataset = tmp_path / "agent.json"
    rag_dataset.write_text("{}", encoding="utf-8")
    agent_dataset.write_text("{}", encoding="utf-8")

    async def run_agent(_: Path) -> dict[str, object]:
        return _agent_report()

    monkeypatch.setattr(evaluate_m5, "run_agent_evaluation", run_agent)
    report = await evaluate_m5.run_unified_evaluation(
        rag_dataset=rag_dataset,
        agent_dataset=agent_dataset,
        include_rag=False,
    )

    assert report.status == "blocked_external"
    assert report.summary["passed"] is False
    assert report.summary["rag_included"] is False
    assert "rag_recall_at_k" in report.targets
    assert report.provenance.input_sha256 == report.dataset_sha256
    assert report.provenance.payload_sha256
    assert verify_report_payload(report.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_unified_report_keeps_unmeasured_citations_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rag_dataset = tmp_path / "rag.json"
    agent_dataset = tmp_path / "agent.json"
    rag_dataset.write_text("{}", encoding="utf-8")
    agent_dataset.write_text("{}", encoding="utf-8")

    async def run_agent(_: Path) -> dict[str, object]:
        return _agent_report()

    async def run_rag(_: Path) -> dict[str, object]:
        return {
            "dataset_version": "rag-v1",
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "ndcg_at_k": 1.0,
            "refusal_precision": 1.0,
            "refusal_recall": 1.0,
            "citation_precision": None,
        }

    monkeypatch.setattr(evaluate_m5, "run_agent_evaluation", run_agent)
    monkeypatch.setattr(evaluate_m5, "run_live_evaluation", run_rag)
    report = await evaluate_m5.run_unified_evaluation(
        rag_dataset=rag_dataset,
        agent_dataset=agent_dataset,
    )

    assert report.status == "passed"
    assert report.measured["rag_citation_precision"] is None
    assert any("unmeasured" in limitation for limitation in report.limitations)


def test_evaluation_command_redacts_dataset_and_report_paths(tmp_path: Path) -> None:
    args = argparse.Namespace(
        rag_dataset=tmp_path / "private-rag.json",
        agent_dataset=tmp_path / "private-agent.json",
        skip_rag=False,
        report_path=tmp_path / "private-report.json",
    )

    encoded = " ".join(evaluate_m5._report_command(args))
    assert str(tmp_path) not in encoded
    assert "<rag-dataset>" in encoded
    assert "<agent-dataset>" in encoded
    assert "<report-path>" in encoded
