from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from scripts.evaluate_analysis_holdout import (
    AnalysisEvaluationError,
    AnalysisHoldoutPlan,
    build_configuration_preflight,
    build_execution_report,
    load_frozen_pack,
    shared_budget_snapshot,
)
from scripts.score_analysis_holdout import AnalysisScoreError, score_analysis_holdout
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import DatabaseSettings, FoundationSettings
from enterprise_doc_core.identity.models import Tenant

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / ".trellis/tasks/10-11-document-analysis-workspace/materials/validation-01"


def _plan() -> AnalysisHoldoutPlan:
    return AnalysisHoldoutPlan.model_validate(
        {
            "schema_version": 1,
            "suite_id": "analysis-validation-01",
            "pack_manifest": (
                ".trellis/tasks/10-11-document-analysis-workspace/materials/"
                "validation-01/manifest.json"
            ),
            "pack_manifest_sha256": (
                "9ffa794efb7c8231e505cfb0e5e07428ebd58d7b3bd07c789b63c1ceecc80008"
            ),
            "reference_sha256": (
                "f3a94f1fc27577ab6974e46e13dce92baa23fede598c1d18463c7daec516bf17"
            ),
            "reference_bytes": 7527,
            "base_url": "http://127.0.0.1:8000",
            "token_env": "ANALYSIS_HOLDOUT_TOKEN",
            "project_id": "10000000-0000-0000-0000-000000000001",
            "snapshot_id": "20000000-0000-0000-0000-000000000002",
            "modes": ["fixed", "agent"],
            "expected_policy": {
                "route_id": "analysis-primary",
                "model": "gpt-5.6-sol",
                "model_revision": None,
                "streaming": False,
                "reasoning_effort": None,
                "max_dispatches_per_unit": 2,
            },
            "max_live_dispatches": 120,
            "poll_seconds": 0.25,
            "run_timeout_seconds": 3600,
        }
    )


def _terminal_arm(mode: str, *, run_id: str) -> dict[str, object]:
    rows = []
    manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    for obj in manifest["objects"]:
        for field in manifest["fields"]:
            rows.append(
                {
                    "object_id": obj["id"],
                    "field_id": field["id"],
                    "status": "succeeded",
                    "dispatches": 1,
                    "error_code": None,
                    "result": {
                        "action": "abstain",
                        "value": None,
                        "evidence_state": "insufficient",
                        "reason": "No bounded evidence.",
                    },
                }
            )
    return {
        "mode": mode,
        "status": "completed",
        "run_id": run_id,
        "latency_ms": 1000.0,
        "policy": {
            "route_id": "analysis-primary",
            "model": "gpt-5.6-sol",
            "prompt_version": ("analysis.fixed.v1" if mode == "fixed" else "analysis.agent.v1"),
        },
        "usage": {
            "dispatches": 30,
            "known_total_tokens": 300,
            "unknown_token_dispatches": 0,
            "estimated_cost": None,
        },
        "rows": rows,
    }


def test_pack_loader_freezes_complete_denominator_without_loading_reference() -> None:
    loaded = load_frozen_pack(_plan(), root=ROOT)

    assert loaded.pack_id == "validation-01"
    assert loaded.denominator == 30
    assert len(loaded.source_files) == 7
    assert loaded.reference_path == PACK / "reference/reference.json"
    assert set(loaded.row_keys) == {
        (obj, field)
        for obj in ("clickup", "monday", "airtable")
        for field in (
            "scope",
            "pricing_unit",
            "recurring_cost",
            "setup_fee",
            "tax_basis",
            "contract_term",
            "support",
            "security_admin",
            "exclusions",
            "payment_terms",
        )
    }
    serialized = json.dumps(loaded.model_input_manifest(), sort_keys=True)
    assert "reference" not in serialized.lower()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("pack_manifest_sha256", "0" * 64, "pack_manifest_integrity_failed"),
        ("reference_sha256", "0" * 64, "execution_reference_identity_mismatch"),
    ],
)
def test_frozen_pack_and_scorer_reject_identity_mismatch(
    tmp_path: Path, field: str, value: str, error: str
) -> None:
    plan = _plan().model_copy(update={field: value})
    if field == "pack_manifest_sha256":
        with pytest.raises(AnalysisEvaluationError, match=error):
            load_frozen_pack(plan, root=ROOT)
        return
    loaded = load_frozen_pack(plan, root=ROOT)
    report = build_execution_report(plan=plan, pack=loaded, arms={})
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(AnalysisScoreError, match=error):
        score_analysis_holdout(
            execution_path=execution_path,
            reference_path=PACK / "reference/reference.json",
        )


def test_configuration_preflight_blocks_before_network_without_real_route_or_token() -> None:
    preflight = build_configuration_preflight(
        _plan(),
        load_frozen_pack(_plan(), root=ROOT),
        FoundationSettings(_env_file=None),
        token_present=False,
    )

    assert preflight["status"] == "blocked"
    assert preflight["network_requests"] == 0
    assert preflight["external_model_calls"] == 0
    assert set(preflight["blocking_codes"]) >= {
        "analysis_model_route_unconfigured",
        "analysis_auth_token_missing",
        "analysis_execution_disabled",
        "structured_uploads_disabled",
    }


def test_execution_report_retains_all_rows_when_one_arm_is_not_run() -> None:
    plan = _plan()
    loaded = load_frozen_pack(plan, root=ROOT)
    fixed = _terminal_arm("fixed", run_id="30000000-0000-0000-0000-000000000003")
    report = build_execution_report(
        plan=plan,
        pack=loaded,
        arms={"fixed": fixed},
        stopped_reason="provider_generation_budget_exhausted",
    )

    assert report["planned_denominator"] == 60
    assert report["observed_rows"] == 30
    assert report["not_run_rows"] == 30
    assert report["arms"]["agent"]["status"] == "not_run"
    assert len(report["arms"]["agent"]["rows"]) == 30
    assert {row["status"] for row in report["arms"]["agent"]["rows"]} == {"not_run"}
    assert "reference" not in json.dumps(report["model_inputs"], sort_keys=True).lower()


def test_scorer_refuses_unreviewed_reference_and_keeps_full_denominator(tmp_path: Path) -> None:
    plan = _plan()
    loaded = load_frozen_pack(plan, root=ROOT)
    report = build_execution_report(
        plan=plan,
        pack=loaded,
        arms={
            "fixed": _terminal_arm("fixed", run_id="30000000-0000-0000-0000-000000000003"),
            "agent": _terminal_arm("agent", run_id="40000000-0000-0000-0000-000000000004"),
        },
    )
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps(report), encoding="utf-8")

    score = score_analysis_holdout(
        execution_path=execution_path,
        reference_path=PACK / "reference/reference.json",
    )

    assert score["status"] == "blocked_reference_unreviewed"
    assert score["reference_denominator"] == 30
    assert score["annotated_rows"] == 0
    assert score["unscored_rows"] == 30
    assert score["quality"] == {"fixed": None, "agent": None, "delta": None}
    assert UUID(report["arms"]["fixed"]["run_id"])


def test_scorer_reports_quality_thresholds_revision_and_operational_delta(
    tmp_path: Path,
) -> None:
    plan = _plan()
    loaded = load_frozen_pack(plan, root=ROOT)
    fixed = _terminal_arm("fixed", run_id="30000000-0000-0000-0000-000000000003")
    agent = _terminal_arm("agent", run_id="40000000-0000-0000-0000-000000000004")
    fixed["latency_ms"] = 1000.0
    assert isinstance(fixed["usage"], dict)
    fixed["usage"].update(estimated_cost="0.30", currency="USD")
    agent["latency_ms"] = 1500.0
    assert isinstance(agent["usage"], dict)
    agent["usage"].update(
        dispatches=40,
        known_total_tokens=450,
        unknown_token_dispatches=0,
        estimated_cost="0.50",
        currency="USD",
    )
    rows = []
    assert isinstance(agent["rows"], list)
    for row in agent["rows"]:
        assert isinstance(row, dict) and isinstance(row["result"], dict)
        expected = {
            "action": row["result"]["action"],
            "value": row["result"]["value"],
            "evidence_state": row["result"]["evidence_state"],
            "required_quote_sha256": [],
        }
        rows.append(
            {
                "object_id": row["object_id"],
                "field_id": row["field_id"],
                "annotation_status": "reviewed",
                "expected_result": expected,
            }
        )
    reference = {
        "denominator": 30,
        "annotated_rows": 30,
        "independently_reviewed": True,
        "rows": rows,
    }
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    report = build_execution_report(plan=plan, pack=loaded, arms={"fixed": fixed, "agent": agent})
    raw = reference_path.read_bytes()
    report["reference_boundary"].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps(report), encoding="utf-8")

    score = score_analysis_holdout(execution_path=execution_path, reference_path=reference_path)

    assert score["status"] == "passed_quality_thresholds_requires_human_acceptance"
    assert score["quality"]["agent"]["no_substantive_revision_rate"] == 1.0
    assert score["quality"]["agent"]["serious_error_rows"] == 0
    assert score["quality"]["agent"]["missing_conflict_preservation"]["retention_rate"] == 1.0
    assert score["comparison"] == {
        "substantive_revision_rows_delta_agent_minus_fixed": 0,
        "latency_ms_delta_agent_minus_fixed": 500.0,
        "dispatches_delta_agent_minus_fixed": 10,
        "known_total_tokens_delta_agent_minus_fixed": 150,
        "estimated_cost_delta_agent_minus_fixed": "0.20",
        "currency": "USD",
    }


async def test_shared_generation_budget_counts_agent_dispatches_across_tenants(
    browser_db,
) -> None:
    tenant_ids = (uuid4(), uuid4())
    now = datetime.now(UTC)
    async with browser_db.sessions.begin() as session:
        session.add_all(
            Tenant(id=value, name="budget tenant", slug=f"budget-{value.hex}", quota_bytes=1024)
            for value in tenant_ids
        )
        await session.flush()
        session.add_all(
            ProviderDispatch(
                tenant_id=tenant_id,
                operation_id=uuid4(),
                kind="agent",
                provider="test",
                model="test",
                channel_hash="0" * 64,
                state="responded",
                started_at=now,
                finished_at=now,
            )
            for tenant_id in tenant_ids
        )
    database_url = make_url(
        browser_db.engine.url.render_as_string(hide_password=False)
    ).update_query_dict({"options": f"-csearch_path={browser_db.schema},public"})
    settings = FoundationSettings(
        _env_file=None,
        database=DatabaseSettings(url=database_url.render_as_string(hide_password=False)),
    )
    try:
        budget = await shared_budget_snapshot(settings=settings, required_dispatches=198)
        assert budget["provider_agent_dispatches"] == 2
        assert budget["remaining"] == 198
        with pytest.raises(
            AnalysisEvaluationError, match="provider_generation_budget_insufficient"
        ):
            await shared_budget_snapshot(settings=settings, required_dispatches=199)
    finally:
        async with browser_db.sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
