from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_the_reproducible_foundation_sequence() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_commands = [
        "uv sync --frozen",
        "pnpm install --frozen-lockfile",
        "docker compose -f infra/compose/docker-compose.yml config",
        "uv run alembic upgrade head",
        "uv run alembic downgrade base",
        "uv run python scripts/foundation_smoke.py --preflight",
        "uv run python scripts/foundation_smoke.py --run",
        "pnpm quality",
    ]

    for command in required_commands:
        assert command in readme
    assert "M1-M7" in readme
    assert "not implemented" in readme.lower()


def test_foundation_smoke_exposes_real_run_path() -> None:
    source = (ROOT / "scripts/foundation_smoke.py").read_text(encoding="utf-8")

    assert '"--run"' in source
    assert '["docker", "compose", "-f"' in source
    assert '["uv", "run", "alembic", "upgrade", "head"]' in source
    assert '["uv", "run", "enterprise-doc-api"]' in source
    assert '["uv", "run", "enterprise-doc-worker"]' in source
    assert '["pnpm", "--filter", "web", "dev"]' in source
    assert "http://127.0.0.1:8000/health/ready" in source
    assert "http://127.0.0.1:8081/health/ready" in source
    assert "http://127.0.0.1:5173/" in source


def test_trellis_package_mappings_match_the_real_monorepo() -> None:
    config = yaml.safe_load((ROOT / ".trellis/config.yaml").read_text(encoding="utf-8"))
    packages = config["packages"]

    assert {name: item["path"] for name, item in packages.items()} == {
        "api": "apps/api",
        "worker": "apps/worker",
        "web": "apps/web",
        "core": "packages/core",
        "infrastructure": "infra",
        "foundation-tests": "tests",
        "mcp": "apps/mcp",
    }
    assert config["default_package"] == "core"
    package_specs = {
        "api": "backend",
        "worker": "backend",
        "web": "frontend",
        "core": "backend",
        "infrastructure": "backend",
        "foundation-tests": "backend",
        "mcp": "backend",
    }
    for package, layer in package_specs.items():
        assert (ROOT / f".trellis/spec/{package}/{layer}/index.md").is_file()


def test_backend_and_frontend_specs_are_factual_and_non_placeholder() -> None:
    spec_files = sorted((ROOT / ".trellis/spec/backend").glob("*.md")) + sorted(
        (ROOT / ".trellis/spec/frontend").glob("*.md")
    )
    forbidden = ["to be filled", "(to be filled", "replace with your actual", "<!--"]

    assert spec_files
    for path in spec_files:
        content = path.read_text(encoding="utf-8")
        lowered = content.lower()
        assert [token for token in forbidden if token in lowered] == [], path
        assert "## Proven Examples" in content, path
        assert "apps/" in content or "packages/" in content or "tests/" in content, path


def test_thinking_guide_index_names_the_project_specific_triggers() -> None:
    content = (ROOT / ".trellis/spec/guides/index.md").read_text(encoding="utf-8")

    assert "Enterprise Document Agent M0" in content
    assert "health contract" in content.lower()
    assert "secret" in content.lower()
