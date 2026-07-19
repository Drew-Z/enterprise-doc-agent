# Backend Quality Guidelines

## Required Gates

Run from the repository root:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
uv run pytest -m "not integration"
```

Ruff excludes generated Trellis/Codex runtime files and checks project Python code.
Mypy runs in strict mode. Integration tests are marked explicitly and require the
local Compose stack.

## Testing

- Unit tests inject health checkers, exporters, and process boundaries.
- ASGI tests call application factories without opening real infrastructure sockets.
- Foundation integration tests verify Compose, migration, and runtime readiness.
- Agent/MCP integration tests verify checkpoint recovery, approval, stdio policy, and artifacts.
- Browser E2E verifies upload recovery and Agent approval/download workflows.
- Test filenames are unique across workspace roots.
- Do not hide failures with skip, xfail, rerun plugins, or allow-failure CI steps.

## Review Checklist

Confirm package ownership, typed settings, bounded I/O, deterministic tests, secret
redaction, stable public schemas, and M0/M1-M7 scope boundaries.

## Forbidden Patterns

Do not import API from Worker or Worker from API, construct global network clients at
module import, log raw exceptions containing secrets, or edit an applied migration.

## Proven Examples

- `pyproject.toml`
- `.github/workflows/quality.yml`
- `tests/foundation/test_ci_contract.py`
- `packages/core/tests/test_health.py`
