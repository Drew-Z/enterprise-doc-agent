from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def test_required_repository_paths_exist() -> None:
    required = [
        "apps/api/pyproject.toml",
        "apps/mcp/pyproject.toml",
        "apps/worker/pyproject.toml",
        "apps/web/package.json",
        "packages/core/pyproject.toml",
        "infra/compose/docker-compose.yml",
        "pnpm-workspace.yaml",
        "package.json",
        "uv.lock",
        "pnpm-lock.yaml",
        ".env.example",
        ".gitignore",
    ]

    missing = [path for path in required if not (ROOT / path).is_file()]
    assert missing == [], f"Missing repository contract files: {missing}"


def test_python_workspace_and_package_names_are_stable() -> None:
    root = read_toml(ROOT / "pyproject.toml")
    project = root["project"]
    uv_config = root["tool"]["uv"]  # type: ignore[index]
    workspace = uv_config["workspace"]  # type: ignore[index]

    assert project["requires-python"] == ">=3.12,<3.13"  # type: ignore[index]
    assert set(project["dependencies"]) == {  # type: ignore[index]
        "enterprise-doc-api",
        "enterprise-doc-core",
        "enterprise-doc-mcp",
        "enterprise-doc-worker",
    }
    assert workspace["members"] == [  # type: ignore[index]
        "apps/api",
        "apps/mcp",
        "apps/worker",
        "packages/core",
    ]
    assert set(uv_config["sources"]) == {  # type: ignore[index]
        "enterprise-doc-api",
        "enterprise-doc-core",
        "enterprise-doc-mcp",
        "enterprise-doc-worker",
    }

    packages = {
        "apps/api/pyproject.toml": "enterprise-doc-api",
        "apps/mcp/pyproject.toml": "enterprise-doc-mcp",
        "apps/worker/pyproject.toml": "enterprise-doc-worker",
        "packages/core/pyproject.toml": "enterprise-doc-core",
    }
    for relative_path, expected_name in packages.items():
        package = read_toml(ROOT / relative_path)
        assert package["project"]["name"] == expected_name  # type: ignore[index]


def test_frontend_workspace_and_root_commands_are_stable() -> None:
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    web_package = json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))

    assert root_package["private"] is True
    assert root_package["packageManager"].startswith("pnpm@")
    assert root_package["engines"]["node"].startswith(">=24")
    assert set(root_package["scripts"]) >= {"lint", "typecheck", "test", "build"}
    assert web_package["name"] == "web"


def test_tool_versions_and_environment_template_are_present() -> None:
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert (ROOT / ".node-version").read_text(encoding="utf-8").strip() == "24"
    env_template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "APP_ENV=local" in env_template
    assert "DATABASE__URL=" in env_template
    assert "OBJECT_STORE__SECRET_KEY=" in env_template
