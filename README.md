# Enterprise Document Agent Platform

This repository is a modular monorepo for an enterprise document Agent platform.
M0 implements only the reproducible foundation: typed configuration, PostgreSQL
with pgvector, Redis, MinIO, API and Worker health contracts, structured logging,
optional OpenTelemetry bootstrap, and a React readiness dashboard.

Upload, durable jobs, RAG, Agent workflows, MCP, production deployment, and the
other M1-M7 capabilities are not implemented in M0.

## Prerequisites

- Python 3.12
- uv 0.11.3
- Node.js 24
- pnpm 11.9.0
- Docker with Compose v2

## Locked Installation

Run from the repository root:

```powershell
uv sync --frozen
pnpm install --frozen-lockfile
```

The root Python project depends on the API, Worker, and Core workspace packages,
so the plain `uv sync --frozen` command installs the complete backend environment.

## Local Configuration

The local defaults are development-only. To override them, create an untracked
`.env` from `.env.example`. Non-local environments reject the known development
credentials.

Validate the Compose model and local prerequisites:

```powershell
docker compose -f infra/compose/docker-compose.yml config
uv run python scripts/foundation_smoke.py --preflight
```

## Complete Foundation Smoke

The smoke command starts healthy infrastructure, initializes the MinIO buckets,
applies the migration, starts API, Worker, and Web, verifies their real endpoints,
and then cleans up its application processes and Compose services without deleting
named volumes:

```powershell
uv run python scripts/foundation_smoke.py --run
```

The command exits non-zero if any tool, port, migration, process, readiness check,
or Web availability check fails.

## Manual Development

Start infrastructure and initialize buckets:

```powershell
docker compose -f infra/compose/docker-compose.yml up -d --wait
docker compose -f infra/compose/docker-compose.yml --profile init run --rm minio-init
```

Manage the M0 migration:

```powershell
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

Run each application in a separate terminal:

```powershell
uv run enterprise-doc-api
uv run enterprise-doc-worker
pnpm dev:web
```

The local endpoints are:

- API live: `http://127.0.0.1:8000/health/live`
- API ready: `http://127.0.0.1:8000/health/ready`
- Worker live: `http://127.0.0.1:8081/health/live`
- Worker ready: `http://127.0.0.1:8081/health/ready`
- Web dashboard: `http://127.0.0.1:5173`

Stop infrastructure without deleting volumes:

```powershell
docker compose -f infra/compose/docker-compose.yml down
```

## Quality Gates

Run the complete backend and frontend matrix:

```powershell
pnpm quality
```

The individual backend commands are:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/core/src apps/api/src apps/worker/src
uv run pytest -m "not integration"
```

The individual frontend commands are:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

GitHub Actions runs independent backend and frontend jobs from `uv.lock` and
`pnpm-lock.yaml`. It has no allow-failure or retry-to-green path.

## Repository Boundaries

- `apps/api`: FastAPI routes and request middleware
- `apps/worker`: long-running Worker lifecycle and internal probes
- `apps/web`: React operational dashboard
- `packages/core`: shared settings, health adapters, database, logging, context, telemetry
- `infra/compose`: PostgreSQL/pgvector, Redis, MinIO, bucket initialization
- `tests/foundation`: repository, migration, runtime, CI, documentation, and evidence contracts

The API and Worker may depend on Core. They do not import each other. Web calls
only the API health contract.
