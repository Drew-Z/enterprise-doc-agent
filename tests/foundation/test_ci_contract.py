from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"

BACKEND_COMMANDS = [
    "uv run ruff format --check .",
    "uv run ruff check .",
    "uv run mypy packages/core/src apps/api/src apps/worker/src",
    'uv run pytest -m "not integration"',
]
FRONTEND_COMMANDS = [
    "pnpm lint",
    "pnpm typecheck",
    "pnpm test",
    "pnpm build",
]


def _workflow() -> dict[str, object]:
    with WORKFLOW.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


def _run_commands(job: object) -> list[str]:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [str(step["run"]).strip() for step in steps if isinstance(step, dict) and "run" in step]


def test_root_quality_commands_cover_backend_frontend_and_smoke() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert scripts["backend:format"] == BACKEND_COMMANDS[0]
    assert scripts["backend:lint"] == BACKEND_COMMANDS[1]
    assert scripts["backend:typecheck"] == BACKEND_COMMANDS[2]
    assert scripts["backend:test"] == BACKEND_COMMANDS[3]
    assert scripts["smoke:foundation"] == "uv run python scripts/foundation_smoke.py --run"
    assert (
        scripts["smoke:multipart"]
        == "uv run python scripts/multipart_smoke.py --run --size-bytes 1073741824 "
        "--interrupt-after-parts 2 --measure-api-rss"
    )
    assert set(scripts) >= {"lint", "typecheck", "test", "build", "quality"}


def test_quality_workflow_has_locked_independent_jobs() -> None:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert set(jobs) >= {"backend", "frontend", "m1-integration"}

    backend_commands = _run_commands(jobs["backend"])
    frontend_commands = _run_commands(jobs["frontend"])

    assert "uv sync --frozen" in backend_commands
    assert "pnpm install --frozen-lockfile" in frontend_commands
    for command in BACKEND_COMMANDS:
        assert command in backend_commands
    for command in FRONTEND_COMMANDS:
        assert command in frontend_commands

    integration_commands = _run_commands(jobs["m1-integration"])
    assert "uv sync --frozen" in integration_commands
    assert "uv run pytest tests/multipart -m integration" in integration_commands
    assert any(
        command.startswith("uv run python scripts/multipart_smoke.py --run")
        and "--size-bytes 17825792" in command
        and "--interrupt-after-parts 1" in command
        and "--measure-api-rss" in command
        for command in integration_commands
    )


def test_quality_workflow_has_no_allow_failure_or_retry_path() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    forbidden = [
        "continue-on-error",
        "|| true",
        "set +e",
        "retry-action",
        "action-retry",
        "--reruns",
    ]

    assert [token for token in forbidden if token in text] == []
