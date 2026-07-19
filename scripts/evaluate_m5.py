from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path
from typing import Any

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.evaluation import EvaluationCase, EvaluationReport
from enterprise_doc_core.evaluation.contracts import utc_now

try:
    from scripts.evaluate_m3_retrieval import run_live_evaluation
    from scripts.evaluate_m4_agent import run_evaluation as run_agent_evaluation
except ModuleNotFoundError:
    from evaluate_m3_retrieval import run_live_evaluation
    from evaluate_m4_agent import run_evaluation as run_agent_evaluation


def _dataset_hash(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _agent_cases(report: dict[str, Any]) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for raw in report.get("cases", []):
        if not isinstance(raw, dict):
            continue
        case_id = raw.get("case_id")
        passed = raw.get("passed")
        if not isinstance(case_id, str) or not isinstance(passed, bool):
            continue
        measured = raw.get("observed", {})
        cases.append(
            EvaluationCase(
                case_id=case_id,
                passed=passed,
                measured=measured if isinstance(measured, dict) else {},
                failure=None if passed else "agent safety contract failed",
            )
        )
    return cases


async def run_unified_evaluation(
    *,
    rag_dataset: Path,
    agent_dataset: Path,
    include_rag: bool = True,
) -> EvaluationReport:
    started_at = utc_now()
    settings = FoundationSettings(_env_file=None)
    agent = await run_agent_evaluation(agent_dataset)
    rag: dict[str, Any] | None = None
    if include_rag:
        rag = await run_live_evaluation(rag_dataset)

    targets: dict[str, float | int | bool | str | None] = {
        "rag_recall_at_k": 0.85,
        "rag_mrr": 0.80,
        "rag_refusal_recall": 1.0,
        "agent_safety_all_cases_pass": True,
    }
    measured: dict[str, float | int | bool | str | None] = {
        "agent_safety_all_cases_pass": bool(agent.get("passed")),
        "agent_cases_passed": int(agent.get("summary", {}).get("passed", 0)),
        "agent_cases_failed": int(agent.get("summary", {}).get("failed", 0)),
    }
    rag_passed = True
    if rag is not None:
        measured.update(
            {
                "rag_recall_at_k": float(rag["recall_at_k"]),
                "rag_mrr": float(rag["mrr"]),
                "rag_ndcg_at_k": float(rag["ndcg_at_k"]),
                "rag_refusal_precision": float(rag["refusal_precision"]),
                "rag_refusal_recall": float(rag["refusal_recall"]),
                "rag_citation_precision": (
                    float(rag["citation_precision"])
                    if isinstance(rag.get("citation_precision"), (float, int))
                    else None
                ),
            }
        )
        rag_passed = (
            float(rag["recall_at_k"]) >= 0.85
            and float(rag["mrr"]) >= 0.80
            and float(rag["refusal_recall"]) >= 1.0
        )

    passed = rag is not None and bool(agent.get("passed")) and rag_passed
    datasets = (agent_dataset,) if rag is None else (rag_dataset, agent_dataset)
    limitations = [
        "The Agent suite uses the deterministic local model gateway and does not measure "
        "real-provider answer quality or cost.",
        "The retrieval suite uses controlled eight-dimensional vectors and does not measure "
        "production embedding quality.",
        "This command is a quality and safety regression, not a load or production-capacity test.",
    ]
    if rag is None:
        limitations.append(
            "RAG evaluation was explicitly skipped and is not satisfied by this report."
        )
    elif measured["rag_citation_precision"] is None:
        limitations.append(
            "The live retrieval suite did not generate answer citations, so citation precision "
            "is reported as unmeasured and is not a passing quality claim."
        )
    return EvaluationReport(
        suite="m5-unified-rag-agent-safety",
        status=("blocked_external" if rag is None else ("passed" if passed else "failed")),
        dataset_version="+".join(
            value
            for value in (
                str(rag.get("dataset_version")) if rag is not None else None,
                str(agent.get("dataset_version")),
            )
            if value is not None
        ),
        dataset_sha256=_dataset_hash(datasets),
        behavior_versions={
            "graph": settings.agent.graph_version,
            "prompt": settings.agent.prompt_version,
            "tool_schema": settings.agent.tool_schema_version,
            "model_provider": settings.model.provider.value,
            "model": settings.model.model_name or "deterministic-grounded",
        },
        targets=targets,
        measured=measured,
        summary={
            "passed": passed,
            "rag_included": rag is not None,
            "agent_case_count": int(agent.get("summary", {}).get("total", 0)),
        },
        cases=_agent_cases(agent),
        limitations=limitations,
        started_at=started_at,
        completed_at=utc_now(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the versioned M5 RAG and Agent evaluation")
    parser.add_argument(
        "--rag-dataset",
        type=Path,
        default=Path("evaluation/m3_retrieval_live_v2.json"),
    )
    parser.add_argument(
        "--agent-dataset",
        type=Path,
        default=Path("evaluation/m4_agent_safety_v1.json"),
    )
    parser.add_argument("--skip-rag", action="store_true")
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()

    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        report = runner.run(
            run_unified_evaluation(
                rag_dataset=args.rag_dataset,
                agent_dataset=args.agent_dataset,
                include_rag=not args.skip_rag,
            )
        )
    rendered = report.model_dump_json(indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report.status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
