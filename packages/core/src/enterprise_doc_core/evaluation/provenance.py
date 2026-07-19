from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

from pydantic import BaseModel

from enterprise_doc_core.evaluation.contracts import ReportProvenance


def capture_report_provenance(
    *,
    command: list[str],
    root: Path,
    execution_scope: str,
    input_sha256: str | None = None,
) -> ReportProvenance:
    if not command or any(not item for item in command):
        raise ValueError("report command must contain non-empty arguments")
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return ReportProvenance(
        command=command,
        environment={
            "operating_system": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "execution_scope": execution_scope,
        },
        commit_sha=commit_sha,
        working_tree_dirty=dirty,
        input_sha256=input_sha256,
    )


def _canonical_payload(payload: dict[str, object]) -> bytes:
    canonical = json.loads(json.dumps(payload, default=str))
    provenance = canonical.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("report payload must contain provenance")
    provenance["payload_sha256"] = None
    return json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def seal_report_payload(payload: dict[str, object]) -> dict[str, object]:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("report payload must contain provenance")
    sealed = json.loads(json.dumps(payload, default=str))
    if not isinstance(sealed, dict):
        raise ValueError("report payload must be a JSON object")
    sealed_provenance = sealed.get("provenance")
    if not isinstance(sealed_provenance, dict):
        raise ValueError("report payload must contain provenance")
    sealed_provenance["payload_sha256"] = hashlib.sha256(_canonical_payload(sealed)).hexdigest()
    return sealed


def seal_report[T: BaseModel](report: T) -> T:
    sealed = seal_report_payload(report.model_dump(mode="json"))
    return type(report).model_validate(sealed)


def verify_report_payload(payload: dict[str, object]) -> bool:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        return False
    digest = provenance.get("payload_sha256")
    return (
        isinstance(digest, str)
        and digest == hashlib.sha256(_canonical_payload(payload)).hexdigest()
    )
