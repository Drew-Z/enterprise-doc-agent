# Enterprise Document Agent Platform

This repository is a modular monorepo for an enterprise document Agent platform.
M0 provides the reproducible foundation. M1 adds the first tenant-scoped business
workflow: authenticated resumable TXT/PDF/DOCX multipart upload, direct browser-to-
MinIO transfer, deterministic completion, cleanup, and an operational React workspace.

M1 ends at an uploaded `DocumentVersion`; M2 adds durable jobs and the M3 branch adds
document ingestion plus deterministic hybrid retrieval. The current branch still uses
a deterministic hash embedding fixture and does not claim a production LLM Agent,
MCP, or deployment platform until those milestones are implemented and evidenced.
The parent roadmap still spans M1-M7; later milestones remain scope, not measured facts.
M4-M7 are not implemented on this branch.

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

## M1 Multipart Smoke

The M1 smoke generates deterministic TXT bytes without materializing a source file,
creates a tenant-scoped upload session, sends each part directly to MinIO through a
checksum-bound presigned URL, stops and restarts the API after the configured number
of parts, reconciles the stored parts, completes the upload, and retries completion.

Check tools, ports, host free space, and Docker availability:

```powershell
uv run python scripts/multipart_smoke.py --preflight --size-bytes 1073741824 --interrupt-after-parts 2
```

Compose host-port overrides are supported. Override the matching application URL at
the same time; for example, use `REDIS_PORT=6380` together with
`REDIS__URL=redis://127.0.0.1:6380/0` when the default host port is occupied.

Run the required 1 GiB evidence path and record sanitized API RSS measurements:

```powershell
uv run python scripts/multipart_smoke.py --run --size-bytes 1073741824 --interrupt-after-parts 2 --measure-api-rss --report-path tmp/m1-multipart-smoke-report.json
```

The command owns local Compose and API processes, stops them without deleting named
volumes, and retains raw API logs only on failure. Its report excludes credentials,
signed URLs, object-store identifiers, user filenames, and content hashes. One local
1 GiB run is evidence for that execution only; it is not a load test or a production
capacity claim.

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
uv run enterprise-doc-worker-consumer
pnpm dev:web
```

`enterprise-doc-worker` owns the probe and Outbox publisher. The separate
`enterprise-doc-worker-consumer` process is the Celery consumer that executes queued
jobs; run at least one consumer alongside the publisher. The current consumer uses
Celery's serialized `solo` pool for the asynchronous handler; scaling out is done by
starting additional consumer processes.

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
`pnpm-lock.yaml`. The `m1-integration` job also starts real PostgreSQL and MinIO,
runs the multipart integration suite, then executes a two-part restart/resume smoke.
The smaller CI payload is a fast regression gate and does not replace the required
local 1 GiB evidence run. No job has an allow-failure or retry-to-green path.

## Repository Boundaries

- `apps/api`: FastAPI routes and request middleware
- `apps/worker`: long-running Worker lifecycle and internal probes
- `apps/web`: React operational upload workspace and readiness dashboard
- `packages/core`: shared settings, health adapters, database, logging, context, telemetry
- `infra/compose`: PostgreSQL/pgvector, Redis, MinIO, bucket initialization
- `tests/foundation`: repository, migration, runtime, CI, documentation, and M0 evidence contracts
- `tests/multipart`: M1 unit, API, PostgreSQL/MinIO, recovery, cleanup, and evidence contracts

The API and Worker may depend on Core. They do not import each other. Web uses the
API control plane while document bytes travel directly to the configured object store.
