from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]

Phase = Literal["prerequisites", "migration", "workloads"]
MIGRATION_NAME = "enterprise-doc-migrate"
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    value = document.get("metadata")
    return value if isinstance(value, dict) else {}


def select_phase(documents: list[dict[str, Any]], phase: Phase) -> list[dict[str, Any]]:
    migration = [
        document
        for document in documents
        if document.get("kind") == "Job" and _metadata(document).get("name") == MIGRATION_NAME
    ]
    if len(migration) != 1:
        raise ValueError("rendered manifests must contain exactly one migration Job")
    if phase == "migration":
        return migration
    if phase == "prerequisites":
        selected = [
            document
            for document in documents
            if document.get("kind") not in {*WORKLOAD_KINDS, "Job"}
        ]
        if any(document.get("kind") in WORKLOAD_KINDS for document in selected):
            raise ValueError("prerequisite phase must not contain workloads")
        return selected
    return [document for document in documents if document.get("kind") in WORKLOAD_KINDS]


def render_phase(source: Path, destination: Path, phase: Phase) -> None:
    loaded = [
        document
        for document in yaml.safe_load_all(source.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]
    selected = select_phase(loaded, phase)
    if not selected:
        raise ValueError(f"phase {phase} produced no Kubernetes resources")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump_all(selected, sort_keys=False, explicit_start=True),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split rendered Kubernetes YAML into ordered rollout phases"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("prerequisites", "migration", "workloads"),
        required=True,
    )
    args = parser.parse_args()
    render_phase(args.input, args.output, args.phase)


if __name__ == "__main__":
    main()
