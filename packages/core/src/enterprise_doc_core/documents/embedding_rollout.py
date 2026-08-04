from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.documents.embedding_probe import probe
from enterprise_doc_core.documents.reindex import enqueue_reindex

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def _reindex_report(report: dict[str, object]) -> dict[str, object]:
    return {
        key: report[key]
        for key in (
            "status",
            "selected",
            "created",
            "replayed",
            "embedding_model",
            "embedding_dimension",
            "embedding_version",
            "values_redacted",
        )
    }


def _count(report: dict[str, object], key: str) -> int:
    value = report.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"reindex report contains invalid {key}")
    return value


def _elapsed_ms(started: float, clock: Clock) -> float:
    return round((clock() - started) * 1000, 2)


async def _within_deadline[T](
    operation: Callable[[], Awaitable[T]],
    *,
    started: float,
    deadline_seconds: float,
    clock: Clock,
) -> T:
    remaining = deadline_seconds - (clock() - started)
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(operation(), timeout=remaining)


def _failed_report(
    *,
    started: float,
    clock: Clock,
    failed_stage: str,
    error_code: str,
    probe_report: dict[str, object] | None,
    reindex_report: dict[str, Any] | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "failed",
        "failed_stage": failed_stage,
        "error_code": error_code,
        "probe": probe_report,
        "reindex": reindex_report,
        "elapsed_ms": _elapsed_ms(started, clock),
        "values_redacted": True,
    }


async def run_embedding_rollout(
    settings: FoundationSettings,
    *,
    limit: int,
    deadline_seconds: float,
    poll_seconds: float,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = perf_counter,
) -> dict[str, object]:
    started = clock()
    probe_report: dict[str, object] | None = None
    reindex_report: dict[str, Any] | None = None
    stage = "probe"
    try:
        probe_report = await _within_deadline(
            lambda: probe(settings),
            started=started,
            deadline_seconds=deadline_seconds,
            clock=clock,
        )
        if probe_report.get("status") != "passed":
            return _failed_report(
                started=started,
                clock=clock,
                failed_stage=stage,
                error_code="embedding_probe_failed",
                probe_report=probe_report,
                reindex_report=None,
            )

        stage = "reindex_plan"
        initial_plan = _reindex_report(
            await _within_deadline(
                lambda: enqueue_reindex(
                    settings,
                    apply=False,
                    tenant_id=None,
                    limit=limit,
                ),
                started=started,
                deadline_seconds=deadline_seconds,
                clock=clock,
            )
        )
        latest_plan = initial_plan
        attempts: list[dict[str, object]] = []
        reindex_report = {
            "status": "running",
            "initial_plan": initial_plan,
            "attempts": attempts,
            "final_plan": None,
        }

        while _count(latest_plan, "selected") > 0:
            if clock() - started >= deadline_seconds:
                reindex_report.update({"status": "timed_out", "final_plan": latest_plan})
                return _failed_report(
                    started=started,
                    clock=clock,
                    failed_stage="reindex_drain",
                    error_code="embedding_reindex_timed_out",
                    probe_report=probe_report,
                    reindex_report=reindex_report,
                )

            stage = "reindex_apply"
            applied = _reindex_report(
                await _within_deadline(
                    lambda: enqueue_reindex(
                        settings,
                        apply=True,
                        tenant_id=None,
                        limit=limit,
                    ),
                    started=started,
                    deadline_seconds=deadline_seconds,
                    clock=clock,
                )
            )
            if _count(applied, "selected") != _count(applied, "created") + _count(
                applied, "replayed"
            ):
                reindex_report.update({"status": "failed", "final_plan": latest_plan})
                return _failed_report(
                    started=started,
                    clock=clock,
                    failed_stage=stage,
                    error_code="embedding_reindex_count_mismatch",
                    probe_report=probe_report,
                    reindex_report=reindex_report,
                )
            attempts.append(applied)

            stage = "reindex_drain"
            await _within_deadline(
                lambda: sleep(poll_seconds),
                started=started,
                deadline_seconds=deadline_seconds,
                clock=clock,
            )
            latest_plan = _reindex_report(
                await _within_deadline(
                    lambda: enqueue_reindex(
                        settings,
                        apply=False,
                        tenant_id=None,
                        limit=limit,
                    ),
                    started=started,
                    deadline_seconds=deadline_seconds,
                    clock=clock,
                )
            )

        if clock() - started >= deadline_seconds:
            reindex_report.update({"status": "timed_out", "final_plan": latest_plan})
            return _failed_report(
                started=started,
                clock=clock,
                failed_stage="reindex_drain",
                error_code="embedding_reindex_timed_out",
                probe_report=probe_report,
                reindex_report=reindex_report,
            )
        reindex_report.update({"status": "completed", "final_plan": latest_plan})
        return {
            "schema_version": 1,
            "status": "passed",
            "failed_stage": None,
            "error_code": None,
            "probe": probe_report,
            "reindex": reindex_report,
            "elapsed_ms": _elapsed_ms(started, clock),
            "values_redacted": True,
        }
    except TimeoutError:
        if reindex_report is not None:
            reindex_report["status"] = "timed_out"
        error_code = (
            "embedding_reindex_timed_out"
            if stage.startswith("reindex_")
            else "embedding_rollout_timed_out"
        )
        return _failed_report(
            started=started,
            clock=clock,
            failed_stage=stage,
            error_code=error_code,
            probe_report=probe_report,
            reindex_report=reindex_report,
        )
    except Exception:
        return _failed_report(
            started=started,
            clock=clock,
            failed_stage=stage,
            error_code="embedding_rollout_operation_failed",
            probe_report=probe_report,
            reindex_report=reindex_report,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe the configured embedding provider and converge document reindexing"
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--deadline-seconds", type=float, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    if not 1 <= args.deadline_seconds <= 3600:
        parser.error("--deadline-seconds must be between 1 and 3600")
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")

    started = perf_counter()
    try:
        ensure_asyncio_compatibility()
        settings = FoundationSettings()
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            report = runner.run(
                run_embedding_rollout(
                    settings,
                    limit=args.limit,
                    deadline_seconds=args.deadline_seconds,
                    poll_seconds=args.poll_seconds,
                )
            )
    except Exception:
        report = _failed_report(
            started=started,
            clock=perf_counter,
            failed_stage="configuration",
            error_code="embedding_rollout_configuration_failed",
            probe_report=None,
            reindex_report=None,
        )
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
