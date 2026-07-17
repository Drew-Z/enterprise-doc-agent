# Backend Directory Structure

## Ownership

The backend is a uv workspace with separate src-layout packages.

```text
apps/api/src/enterprise_doc_api/       HTTP application and middleware
apps/worker/src/enterprise_doc_worker/ Worker lifecycle and probe application
packages/core/src/enterprise_doc_core/ Shared foundation contracts
packages/core/src/enterprise_doc_core/db/migrations/ Alembic revisions
tests/foundation/                      Cross-package executable contracts
```

API and Worker may import Core. They do not import each other. App-owned settings,
entry points, and process lifecycle remain inside the owning app.

## Module Rules

- Put reusable settings, health, logging, request context, database, and telemetry
  infrastructure in `enterprise_doc_core`.
- Put FastAPI routes and ASGI middleware in `enterprise_doc_api`.
- Put Worker supervision and internal probes in `enterprise_doc_worker`.
- Add business modules in later milestones instead of expanding a generic utility file.

## Naming

Python modules and packages use snake_case. Tests use unique descriptive file names,
such as `test_api_telemetry.py`, to avoid import collisions across workspace test roots.

## Proven Examples

- `apps/api/src/enterprise_doc_api/middleware/request_context.py`
- `apps/worker/src/enterprise_doc_worker/lifecycle.py`
- `packages/core/src/enterprise_doc_core/telemetry/runtime.py`
