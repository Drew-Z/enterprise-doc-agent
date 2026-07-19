from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "render_k8s_phase.py"
SPEC = spec_from_file_location("render_k8s_phase_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
render_k8s_phase = module_from_spec(SPEC)
sys.modules[SPEC.name] = render_k8s_phase
SPEC.loader.exec_module(render_k8s_phase)


def _document(kind: str, name: str) -> dict[str, object]:
    return {"apiVersion": "v1", "kind": kind, "metadata": {"name": name}}


def test_rollout_phase_split_places_migration_before_workloads() -> None:
    documents = [
        _document("Namespace", "enterprise-doc-agent-staging"),
        _document("ConfigMap", "enterprise-doc-config"),
        _document("ServiceAccount", "enterprise-doc-runtime"),
        _document("Job", "enterprise-doc-migrate"),
        _document("Deployment", "enterprise-doc-api"),
        _document("Deployment", "enterprise-doc-worker"),
        _document("Service", "enterprise-doc-api"),
    ]

    prerequisites = render_k8s_phase.select_phase(documents, "prerequisites")
    migration = render_k8s_phase.select_phase(documents, "migration")
    workloads = render_k8s_phase.select_phase(documents, "workloads")

    assert {item["kind"] for item in prerequisites} == {
        "Namespace",
        "ConfigMap",
        "ServiceAccount",
        "Service",
    }
    assert [item["metadata"]["name"] for item in migration] == ["enterprise-doc-migrate"]
    assert {item["kind"] for item in workloads} == {"Deployment"}
    assert {item["metadata"]["name"] for item in workloads} == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
    }
