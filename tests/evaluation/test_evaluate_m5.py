from __future__ import annotations

from pathlib import Path

import pytest
import scripts.evaluate_m5 as evaluate_m5


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
