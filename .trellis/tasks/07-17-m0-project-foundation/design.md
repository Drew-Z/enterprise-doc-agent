# M0 Project Foundation: Technical Design

## Design Summary

M0 creates a modular monorepo with three runnable applications and one shared Python package:

```text
enterprise-doc-agent/
|-- apps/
|   |-- api/        FastAPI HTTP process
|   |-- worker/     long-lived Worker foundation process
|   `-- web/        React/Vite operational dashboard
|-- packages/
|   `-- core/       shared configuration, infrastructure checks, logging, and tracing
|-- infra/
|   `-- compose/    PostgreSQL/pgvector, Redis, MinIO, and bucket initialization
|-- tests/
|   `-- foundation/ repository and local-stack contracts
|-- pyproject.toml  uv workspace and shared Python quality configuration
|-- pnpm-workspace.yaml
`-- package.json    root frontend scripts only
```

The API and Worker import shared infrastructure code from Core but do not import each other. Web communicates only with the API. M0 deliberately has no durable job or Agent domain model.

## Package and Dependency Boundaries

### Python workspace

- The root `pyproject.toml` defines Python `>=3.12,<3.13`, the uv workspace members, development dependency groups, and shared Ruff, mypy, and pytest configuration.
- `packages/core` is an installable `src`-layout package named `enterprise-doc-core` with import namespace `enterprise_doc_core`.
- `apps/api` and `apps/worker` are separately installable `src`-layout packages and depend on Core through the uv workspace.
- API owns HTTP routes and middleware. Worker owns its process lifecycle and probe server. Core owns reusable configuration models, dependency checker protocols/implementations, logging setup, and OTel bootstrap.
- Core must not contain upload, job, retrieval, Agent, or approval business logic in M0.

### Frontend workspace

- The root pnpm workspace initially contains `apps/web` and records the package-manager version.
- Web uses React, TypeScript, Vite, TanStack Query, Vitest, Testing Library, ESLint, and the existing icon library selected during implementation.
- The first screen is the operational overview. It uses restrained, compact status presentation and does not create fake job or document data.

## Runtime Topology

```text
Browser --HTTP--> API :8000
                    |-- PostgreSQL/pgvector :5432
                    |-- Redis :6379
                    `-- MinIO API :9000

Worker :8081 probes
  |-- PostgreSQL/pgvector
  |-- Redis
  `-- MinIO

Web dev server :5173 --HTTP--> API health endpoint
```

Compose owns only local infrastructure and the one-shot MinIO bucket initializer. API, Worker, and Web run as local development processes in M0; later image/deployment work remains M6 scope.

## Configuration Contract

Core provides typed settings groups for runtime, database, Redis, object storage, and observability. API and Worker extend or compose these groups with app-owned host, port, and service-name settings. Configuration is loaded once at process startup and passed explicitly to factories.

Rules:

1. Credentials use secret-aware types and never appear through `repr`, JSON logs, trace attributes, or health payloads.
2. `.env.example` contains development placeholders only. `.env` is ignored.
3. `APP_ENV=local` may use documented development defaults; non-local startup rejects known development credentials and missing required values.
4. Browser configuration contains only public values such as API base URL.

## Health Contracts

### API

`GET /health/live` returns HTTP 200 while the process can serve requests and does not connect to dependencies.

`GET /health/ready` runs PostgreSQL, Redis, and MinIO checks concurrently with per-check timeouts. The stable response contract is:

```json
{
  "status": "ready | not_ready",
  "checks": {
    "database": {"status": "up | down | timeout"},
    "redis": {"status": "up | down | timeout"},
    "object_store": {"status": "up | down | timeout"}
  }
}
```

HTTP 200 requires `status=ready` and every component `up`. Any `down` or `timeout` returns the same typed body with HTTP 503 and `status=not_ready`. The response never contains credentials, connection strings, stack traces, or business data.

### Worker

The Worker is an asyncio process with a small internal probe server, startup dependency checks, lifecycle logs/spans, signal-aware graceful shutdown, and an empty run loop. It performs no polling, queue consumption, or business work in M0. Worker live/ready semantics match the API.

### Web

Web uses a typed health client and TanStack Query. A pending request maps to loading; HTTP 200 with a valid `ready` body maps to healthy; HTTP 503 with a valid `not_ready` body maps to degraded and shows only component state; network errors, unexpected status codes, and schema-invalid bodies map to unreachable. Manual refresh is an action, not a fifth server state. Web does not call Worker or infrastructure services directly.

## Request Context and Logging

API middleware establishes `request_id` and `correlation_id` through `X-Request-ID` and `X-Correlation-ID`. Missing values are UUIDs. Incoming values are accepted only when they satisfy length and character constraints. Values are stored in context variables, added to response headers, structured request logs, and active spans, then cleared after each request.

Structured logs use JSON with stable keys such as timestamp, level, service, environment, event, request_id, correlation_id, duration_ms, and error_type. Request/response bodies, authorization headers, object-store signatures, DSNs, and secret settings are excluded.

## OpenTelemetry Foundation

- Core exposes idempotent telemetry initialization and shutdown.
- Disabled mode has no exporter and cannot fail startup.
- Tests use an in-memory span exporter.
- Enabled mode can construct an exporter for a configurable OTLP endpoint, but no collector is started or required by M0.
- API spans use route templates; Worker emits lifecycle spans in M0.
- Document text, prompt text, secrets, and raw errors are forbidden trace attributes.
- Cross-process propagation, collector deployment, trace-backend verification, metrics, dashboards, SLOs, load tests, and fault injection remain M5 scope.

## Database and Migration Design

- SQLAlchemy 2 async engine/session factories live in Core.
- Alembic is callable from the repository root, with revisions stored beside the Core database contract.
- The initial revision enables exactly `CREATE EXTENSION IF NOT EXISTS vector`.
- It creates no Tenant, Document, UploadSession, Job, Chunk, AgentRun, or Approval tables.
- Readiness uses a bounded `SELECT 1`; migration status is verified by the smoke procedure, not every request.
- Later milestones add new revisions and never edit an applied M0 revision.

## Compose Design

Compose provides a pgvector-enabled PostgreSQL image, Redis, MinIO, an idempotent one-shot MinIO bucket initializer, named volumes, an isolated network, environment substitution, and health checks. It contains no application image, Celery worker, OTel collector, Prometheus, Grafana, or Kubernetes resource.

## CI and Validation Design

The baseline GitHub Actions workflow has independent backend and frontend jobs:

```text
backend: uv sync --frozen -> ruff format --check -> ruff check -> mypy -> pytest unit
frontend: pnpm install --frozen-lockfile -> eslint -> tsc --noEmit -> vitest run
```

A local foundation smoke procedure additionally verifies Compose, migration, API readiness, Worker readiness, and Web availability. M6 owns image build, security scanning, Kind, staging, release, and rollback workflows.

## M0 Evidence Contract

The final M0 run writes an immutable manifest at `evidence/m0/<YYYYMMDD-HHMMSS>-m0-project-foundation.json` and adds its relative path plus summary status to `evidence/index.json`. The manifest follows the parent evidence contract and includes:

```text
evidence_id, milestone=M0, requirement_ids, status
command_or_procedure, environment, tool_versions
commit_sha, started_at, completed_at, result_summary
artifacts, limitations, owner
```

Every automated command has an exit code and referenced log/report artifact. The dashboard visual gate records viewport, screenshot paths, reviewer, and result. M0 has no image digest because application images are M6 scope; that omission is stated as `not_applicable`, not left ambiguous.

## Test Boundaries

### Public interfaces under test

- Settings constructors and validation errors.
- Dependency checker protocol and aggregate readiness result.
- API application factory and health responses.
- Request-context middleware headers and log fields.
- Worker application factory, probes, startup, and shutdown.
- Telemetry initializer in disabled and in-memory-exporter modes.
- Web health client and operational overview component.
- Root repository/manifest contract and migration commands.
- M0 evidence manifest and parent evidence index schema.

### Mock boundaries

- Unit tests replace PostgreSQL, Redis, MinIO, clocks, UUID generation, and span exporters through explicit interfaces or factories.
- Unit tests do not open real network connections.
- Foundation smoke tests use real Compose services.
- Web tests mock the HTTP boundary, not TanStack Query internals.

## Compatibility and Evolution

- Health response fields remain stable for Web and future Kubernetes probes; semantic changes require contract-test updates.
- M1-M4 business types live in dedicated modules rather than expanding a generic foundation module.
- The initial Alembic revision is immutable after use.
- Trellis package mappings are added only after M0 creates and validates the real directories.
- Generated bootstrap specs remain placeholders until the final M0 slice records implemented conventions.

### Identity transition

- Health endpoints are unauthenticated infrastructure endpoints and carry request/correlation context only.
- The shared request context exposes an optional, separately typed principal-enrichment boundary; M0 leaves it empty and never substitutes development tenant/actor identifiers.
- Before M1 exposes the first tenant-scoped business route, M1 must implement authentication/principal resolution and prove that `tenant_id` and `actor_id` reach logs, traces, database writes, and authorization checks.

## Rollout and Rollback

1. Bootstrap manifests and repository contracts.
2. Add Core foundation, then API and Worker processes.
3. Add Compose and migration smoke.
4. Add Web shell and CI.
5. Capture proven conventions in Trellis specs.

Each implementation slice should be independently reviewable. Rollback removes the current slice's entry points and manifests without rewriting existing migrations. Before a later milestone depends on M0, rollback must leave locked dependency installation and the last accepted smoke path working.
