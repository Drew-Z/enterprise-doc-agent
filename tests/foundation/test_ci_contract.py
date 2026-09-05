from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
HEAVY_WORKFLOW = ROOT / ".github" / "workflows" / "quality-heavy.yml"
SELF_HOSTED_WORKFLOW = ROOT / ".github" / "workflows" / "quality-self-hosted.yml"
STAGING_RAG_QUALITY_WORKFLOW = ROOT / ".github" / "workflows" / "evaluate-staging-rag-quality.yml"
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


def test_manual_self_hosted_quality_is_serial_and_secret_free() -> None:
    workflow = _workflow(SELF_HOSTED_WORKFLOW)
    assert _triggers(workflow) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert set(jobs) == {"quality"}
    quality = jobs["quality"]
    assert isinstance(quality, dict)
    assert quality["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "enterprise-doc-staging",
    ]
    commands = _run_commands(quality)
    assert "uv sync --frozen" in commands
    assert "pnpm install --frozen-lockfile" in commands
    for command in BACKEND_COMMANDS + FRONTEND_COMMANDS:
        assert command in commands
    assert not any("kubectl" in command or "docker compose" in command for command in commands)
    text = SELF_HOSTED_WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "actions/setup-python" not in text
    assert "astral-sh/setup-uv" not in text
    assert "actions/setup-node" not in text
    assert "pnpm/action-setup" not in text
    assert "/opt/enterprise-doc-toolchain/python/bin/python" in text
    assert "/opt/enterprise-doc-toolchain/python/bin/uv" in text
    assert "/opt/enterprise-doc-toolchain/node/bin" in text
    assert 'PATH="$RUNNER_NODE_BIN:$PATH" $RUNNER_NODE_BIN/pnpm --version' in text
    assert "v24.14.0" in text
    assert "11.9.0" in text


def test_manual_staging_rag_quality_is_serialized_and_secret_scoped() -> None:
    workflow = _workflow(STAGING_RAG_QUALITY_WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}
    assert _triggers(workflow) == {
        "workflow_dispatch": {
            "inputs": {
                "evaluation_scope": {
                    "description": (
                        "trial runs the vetted subset; full runs all 40 reviewed v2 cases"
                    ),
                    "required": True,
                    "default": "trial",
                    "type": "choice",
                    "options": ["trial", "full"],
                },
                "staging_base_url": {
                    "description": "Externally reachable staging API base URL",
                    "required": True,
                    "default": "https://agent.playlab.eu.cc",
                    "type": "string",
                },
            }
        }
    }
    assert workflow["concurrency"] == {
        "group": "enterprise-doc-agent-staging",
        "cancel-in-progress": False,
    }
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"evaluate"}
    evaluate = jobs["evaluate"]
    assert isinstance(evaluate, dict)
    assert evaluate["runs-on"] == ["self-hosted", "linux", "x64", "enterprise-doc-staging"]
    assert evaluate["environment"] == "staging"
    assert evaluate["timeout-minutes"] == 40
    assert evaluate["env"] == {
        "RUNNER_PYTHON": "/opt/enterprise-doc-toolchain/python/bin/python",
        "RUNNER_UV": "/opt/enterprise-doc-toolchain/python/bin/uv",
    }
    steps = evaluate["steps"]
    assert isinstance(steps, list)
    evaluation_step = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Run authenticated staging RAG quality evaluation"
    )
    assert evaluation_step["env"] == {
        "EVALUATION_SCOPE": "${{ inputs.evaluation_scope }}",
        "STAGING_BASE_URL": "${{ inputs.staging_base_url }}",
        "STAGING_SMOKE_TOKEN": "${{ secrets.STAGING_SMOKE_TOKEN }}",
        "STAGING_ALLOWED_HOST": "${{ vars.STAGING_ALLOWED_HOST }}",
        "STAGING_OBJECT_STORE_ALLOWED_HOST": "${{ vars.STAGING_OBJECT_STORE_ALLOWED_HOST }}",
    }
    assert (
        "STAGING_SMOKE_TOKEN" not in evaluate["env"]
        and "STAGING_ALLOWED_HOST" not in evaluate["env"]
        and "STAGING_OBJECT_STORE_ALLOWED_HOST" not in evaluate["env"]
    )
    commands = _run_commands(evaluate)
    assert '"$RUNNER_UV" sync --frozen' in commands
    evaluation = next(
        command for command in commands if "evaluate_staging_rag_quality.py" in command
    )
    assert "evaluation/rag_quality_v2.json" in evaluation
    assert "--trial-only" in evaluation
    assert "--timeout-seconds 1800" in evaluation
    assert "kubectl" not in evaluation
    text = STAGING_RAG_QUALITY_WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.STAGING_KUBECONFIG" not in text
    assert "STAGING_SMOKE_TOKEN" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text


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
        for path in (
            WORKFLOW,
            HEAVY_WORKFLOW,
            SELF_HOSTED_WORKFLOW,
            STAGING_RAG_QUALITY_WORKFLOW,
            CONTAINER_WORKFLOW,
        )
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
