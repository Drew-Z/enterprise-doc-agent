"""Opt-in, bounded SSH observation of the existing single node; no workload or deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.business_capacity_telemetry import (
    normalize_snapshot,
    summarize_observations,
    timestamp,
)
from scripts.capacity_host_probe import digest
from scripts.run_business_capacity import ROOT, validate_output, write_json

PROBE = ROOT / "scripts/capacity_host_probe.py"


class ObservationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    ssh_host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    namespace: str = Field(
        default="enterprise-doc-agent-staging", pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
    )
    samples: int = Field(default=13, ge=2, le=241, strict=True)
    interval_seconds: float = Field(default=5, ge=5, le=60, strict=True)
    max_run_seconds: float = Field(default=120, ge=1, le=3600, strict=True)

    @model_validator(mode="after")
    def check_span(self) -> ObservationPlan:
        if (self.samples - 1) * self.interval_seconds > self.max_run_seconds:
            raise ValueError("observation_window_exceeds_budget")
        return self


def ssh_snapshot(plan: ObservationPlan) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=5",
            plan.ssh_host,
            "python3",
            "-B",
            "-",
            "--namespace",
            plan.namespace,
        ],
        input=PROBE.read_bytes(),
        capture_output=True,
        timeout=25,
        check=True,
    )
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise ValueError("probe_response_too_large")
    return normalize_snapshot(json.loads(completed.stdout))


def run_collection(
    plan: ObservationPlan,
    output: Path,
    *,
    probe: Callable[[ObservationPlan], dict[str, Any]] = ssh_snapshot,
    business_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = validate_output(output)
    output.mkdir()
    records: list[dict[str, Any]] = [{"index": i, "status": "not_run"} for i in range(plan.samples)]
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "read-only-business-resource-observation",
        "status": "running",
        "production_capacity_approved": False,
        "started_at": datetime.now(UTC).isoformat(),
        "plan": plan.model_dump(),
        "records": records,
        "business_report_canonical_sha256": digest(business_report)
        if business_report is not None
        else None,
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [PROBE, Path(__file__), ROOT / "scripts/business_capacity_telemetry.py"]
        },
    }
    write_json(output / "run.json", report)
    started = time.monotonic()
    next_sample_at = started
    try:
        with (output / "samples.jsonl").open("x", encoding="utf-8", newline="\n") as journal:
            for record in records:
                remaining = plan.max_run_seconds - (time.monotonic() - started)
                if remaining < 25:
                    record["reason"] = "run_budget_exhausted"
                else:
                    wait = max(0, next_sample_at - time.monotonic())
                    if wait + 25 > remaining:
                        record["reason"] = "run_budget_exhausted"
                    else:
                        time.sleep(wait)
                        record["requested_at"] = datetime.now(UTC).isoformat()
                        try:
                            snapshot = probe(plan)
                            record["received_at"] = datetime.now(UTC).isoformat()
                            captured = timestamp(snapshot["captured_at"])
                            if (
                                captured - timestamp(record["received_at"])
                            ).total_seconds() > 5 or (
                                timestamp(record["requested_at"]) - captured
                            ).total_seconds() > 5:
                                raise ValueError("remote_clock_mismatch")
                            record.update(status="observed", snapshot=snapshot)
                        except KeyboardInterrupt:
                            record.update(status="interrupted", error_code="probe_interrupted")
                            journal.write(json.dumps(record) + "\n")
                            journal.flush()
                            raise
                        except Exception as error:
                            record.update(
                                status="failed",
                                error_code="probe_failed",
                                error_type=type(error).__name__,
                            )
                        next_sample_at = time.monotonic() + plan.interval_seconds
                journal.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                journal.flush()
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="interrupted", error_type=type(error).__name__)
    finally:
        report["completed_at"] = datetime.now(UTC).isoformat()
        try:
            report["summary"] = summarize_observations(
                records, interval_seconds=plan.interval_seconds, business_report=business_report
            )
            if report["status"] != "interrupted":
                report["status"] = report["summary"]["status"]
        except Exception as error:
            report.update(status="interrupted", summary_error_type=type(error).__name__)
        write_json(output / "run.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--namespace", default="enterprise-doc-agent-staging")
    parser.add_argument("--samples", type=int, default=13)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--max-run-seconds", type=float, default=120)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--business-report", type=Path)
    parser.add_argument("--execute-readonly", action="store_true")
    args = parser.parse_args()
    try:
        plan = ObservationPlan(
            ssh_host=args.ssh_host,
            namespace=args.namespace,
            samples=args.samples,
            interval_seconds=args.interval_seconds,
            max_run_seconds=args.max_run_seconds,
        )
        if not args.execute_readonly:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "plan": plan.model_dump(),
                        "production_capacity_approved": False,
                    }
                )
            )
            return
        if args.output_dir is None:
            raise ValueError("output_directory_required")
        validate_output(args.output_dir)
        business = None
        if args.business_report is not None:
            if args.business_report.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("business_report_too_large")
            business = json.loads(args.business_report.read_bytes())
            if business.get("scope") != "local-controlled-business-sampler":
                raise ValueError("unsupported_business_report")
        result = run_collection(plan, args.output_dir, business_report=business)
    except (ValueError, OSError) as error:
        parser.exit(2, "observation_configuration_rejected (" + type(error).__name__ + ")\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "report": str(args.output_dir / "run.json"),
                "production_capacity_approved": False,
            }
        )
    )
    if result["status"] != "read_only_observed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
