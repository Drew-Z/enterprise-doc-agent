from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
HEAVY_WORKFLOW = ROOT / ".github" / "workflows" / "quality-heavy.yml"
CONTAINER_WORKFLOW = ROOT / ".github" / "workflows" / "container.yml"

BACKEND_COMMANDS = [
    "uv run ruff format --check .",
    "uv run ruff check .",
    "uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src",
    'uv run pytest -m "not integration"',
]
FRONTEND_COMMANDS = [
    "pnpm lint",
    "pnpm typecheck",
    "pnpm test",
    "pnpm build",
]


def _workflow(path: Path = WORKFLOW) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


def _run_commands(job: object) -> list[str]:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [str(step["run"]).strip() for step in steps if isinstance(step, dict) and "run" in step]


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    # PyYAML's YAML 1.1 loader treats the GitHub Actions `on` key as boolean.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


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


def test_quality_workflow_has_only_fast_jobs() -> None:
    workflow = _workflow()
    triggers = _triggers(workflow)
    assert set(triggers) == {"pull_request", "push"}
    assert "**/*.md" in triggers["pull_request"]["paths-ignore"]
    assert ".trellis/tasks/**/*.jsonl" in triggers["push"]["paths-ignore"]
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert set(jobs) == {"backend", "frontend"}

    backend_commands = _run_commands(jobs["backend"])
    frontend_commands = _run_commands(jobs["frontend"])

    assert "uv sync --frozen" in backend_commands
    assert "pnpm install --frozen-lockfile" in frontend_commands
    for command in BACKEND_COMMANDS:
        assert command in backend_commands
    for command in FRONTEND_COMMANDS:
        assert command in frontend_commands


def test_manual_heavy_quality_preserves_integration_and_e2e_evidence() -> None:
    workflow = _workflow(HEAVY_WORKFLOW)
    assert _triggers(workflow) == {"workflow_dispatch": None}
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert set(jobs) == {"m1-integration", "m4-integration", "web-e2e"}

    integration_commands = _run_commands(jobs["m1-integration"])
    assert "uv sync --frozen" in integration_commands
    assert "uv run pytest tests/multipart -m integration" in integration_commands
    port_release_gate = next(
        command for command in integration_commands if "--preflight" in command
    )
    assert "for attempt in $(seq 1 45)" in port_release_gate
    assert "sleep 2" in port_release_gate
    assert "ports were not released within 90 seconds" in port_release_gate
    assert any(
        command.startswith("uv run python scripts/multipart_smoke.py --run")
        and "--size-bytes 17825792" in command
        and "--interrupt-after-parts 1" in command
        and "--measure-api-rss" in command
        for command in integration_commands
    )
    compose_down = integration_commands.index(
        "docker compose -f infra/compose/docker-compose.yml down"
    )
    port_release = integration_commands.index(port_release_gate)
    smoke = next(
        index
        for index, command in enumerate(integration_commands)
        if command.startswith("uv run python scripts/multipart_smoke.py --run")
    )
    assert compose_down < port_release < smoke

    m4_commands = _run_commands(jobs["m4-integration"])
    assert "uv run enterprise-doc-checkpointer-setup --setup" in m4_commands
    assert "uv run enterprise-doc-checkpointer-setup --check" in m4_commands
    assert "uv run pytest -m integration -q" in m4_commands
    assert "uv run python scripts/evaluate_m4_agent.py" in m4_commands

    e2e_commands = _run_commands(jobs["web-e2e"])
    assert "uv sync --frozen" in e2e_commands
    assert "pnpm install --frozen-lockfile" in e2e_commands
    assert "pnpm --filter web exec playwright install --with-deps chromium" in e2e_commands
    assert "pnpm --filter web test:e2e" in e2e_commands

    e2e_steps = jobs["web-e2e"]["steps"]
    assert isinstance(e2e_steps, list)
    evidence = next(
        step
        for step in e2e_steps
        if isinstance(step, dict)
        and step.get("uses") == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert "always()" in str(evidence["if"])
    assert evidence["with"]["path"] == "apps/web/test-results"


def test_container_pull_requests_are_path_filtered() -> None:
    workflow = _workflow(CONTAINER_WORKFLOW)
    triggers = _triggers(workflow)
    pull_request = triggers.get("pull_request")
    assert isinstance(pull_request, dict)
    paths = set(pull_request["paths"])
    assert {
        ".github/workflows/container.yml",
        "infra/docker/**",
        "apps/**",
        "packages/**",
        "tests/deployment/**",
        "uv.lock",
        "pnpm-lock.yaml",
    } <= paths
    assert triggers["push"] == {"tags": ["v*.*.*"]}
    assert "workflow_dispatch" in triggers


def test_quality_workflow_has_no_allow_failure_or_retry_path() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (WORKFLOW, HEAVY_WORKFLOW, CONTAINER_WORKFLOW)
    )
    forbidden = [
        "continue-on-error",
        "|| true",
        "set +e",
        "retry-action",
        "action-retry",
        "--reruns",
    ]

    assert [token for token in forbidden if token in text] == []


def test_vite_prebundles_the_lazy_upload_worker_dependency() -> None:
    vite_config = (ROOT / "apps" / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert "optimizeDeps" in vite_config
    assert 'include: ["hash-wasm"]' in vite_config
