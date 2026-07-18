from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _project(path: str) -> dict[str, object]:
    with (ROOT / path).open("rb") as handle:
        return tomllib.load(handle)


def test_m4_dependencies_pin_stable_major_lines() -> None:
    core = _project("packages/core/pyproject.toml")
    mcp = _project("apps/mcp/pyproject.toml")

    core_dependencies = set(core["project"]["dependencies"])  # type: ignore[index]
    mcp_dependencies = set(mcp["project"]["dependencies"])  # type: ignore[index]

    assert "langgraph>=1.2.9,<2" in core_dependencies
    assert "langgraph-checkpoint-postgres>=3.1,<4" in core_dependencies
    assert "mcp>=1.28.1,<2" in mcp_dependencies


def test_root_workspace_installs_the_mcp_process() -> None:
    root = _project("pyproject.toml")

    dependencies = set(root["project"]["dependencies"])  # type: ignore[index]
    members = set(root["tool"]["uv"]["workspace"]["members"])  # type: ignore[index]
    ruff_sources = set(root["tool"]["ruff"]["src"])  # type: ignore[index]

    assert "enterprise-doc-mcp" in dependencies
    assert "apps/mcp" in members
    assert "apps/mcp/src" in ruff_sources


def test_lockfile_records_exact_m4_dependency_versions() -> None:
    lock = _project("uv.lock")
    packages = {
        package["name"]: package["version"]
        for package in lock["package"]  # type: ignore[index]
        if package["name"]
        in {"enterprise-doc-mcp", "langgraph", "langgraph-checkpoint-postgres", "mcp"}
    }

    assert packages == {
        "enterprise-doc-mcp": "0.1.0",
        "langgraph": "1.2.9",
        "langgraph-checkpoint-postgres": "3.1.0",
        "mcp": "1.28.1",
    }
