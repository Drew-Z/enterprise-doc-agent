# M0 Project Foundation

## Goal

Create a reproducible, typed, testable monorepo foundation for the Enterprise Document Agent Platform. A developer must be able to install locked dependencies, start the required local infrastructure, apply the database migration, run the API, Worker, and Web processes, inspect real health state, and execute the baseline quality checks without any M1-M7 business capability being present.

M0 establishes facts that later milestones can build on. It does not claim that uploads, durable jobs, RAG, Agent workflows, or deployment are implemented.

## Requirements

### Repository and toolchain

- **R-1**: The repository contains `apps/web`, `apps/api`, `apps/worker`, `packages/core`, `infra`, and `tests` with clear ownership boundaries.
- **R-2**: Python 3.12 dependencies are managed by a root uv workspace with committed lock data and separately importable API, Worker, and Core packages.
- **R-3**: Frontend dependencies are managed by a pnpm workspace with a committed lockfile and an explicit Node.js engine requirement.
- **R-4**: Root-level commands document and run backend formatting/linting, backend type checks, backend unit tests, frontend linting, frontend type checks, frontend unit tests, and local foundation smoke checks.

### Local infrastructure and database

- **R-5**: Docker Compose starts PostgreSQL with pgvector, Redis, and MinIO using named volumes, deterministic local ports, health checks, and development-only credentials sourced from environment variables.
- **R-6**: MinIO initialization creates the required development buckets without exposing root credentials to the browser or application logs.
- **R-7**: Alembic owns the database schema lifecycle. The initial migration enables exactly the PostgreSQL `vector` extension without introducing M1-M7 business tables.
- **R-8**: The migration can be applied to an empty database, downgraded while it is still safe to do so, and applied again by documented commands.

### Runtime processes and health

- **R-9**: FastAPI exposes `GET /health/live` and `GET /health/ready`. Liveness reports process availability without calling dependencies; readiness checks required dependencies with bounded timeouts and returns HTTP 503 when the process should not receive traffic.
- **R-10**: The Worker is a separately runnable long-lived process with internal liveness and readiness endpoints. It performs no business jobs in M0.
- **R-11**: The React application is an operational dashboard shell, not a marketing page. It maps the API health contract into loading, healthy, degraded, and unreachable states and provides a manual refresh action.
- **R-12**: API, Worker, and Web startup and shutdown behavior is testable and does not require uncommitted local configuration.

### Configuration, logging, and tracing

- **R-13**: Backend configuration is typed, loaded from environment variables, validates required values at startup, and represents credentials with secret-aware types.
- **R-14**: The repository contains a safe `.env.example`; real `.env` files, credentials, generated artifacts, and local volumes are ignored by Git.
- **R-15**: Every API request has a request ID and correlation ID. Valid incoming identifiers are propagated, missing identifiers are generated, and both are returned in response headers.
- **R-16**: API and Worker logs are structured JSON and include service, environment, request/correlation context where applicable, and error class without logging secret values or document bodies.
- **R-17**: API and Worker initialize OpenTelemetry through a shared helper. Tracing is disabled by default, supports a test exporter, and can construct a configurable OTLP exporter when enabled. M0 proves only SDK/bootstrap and in-process span contracts; it does not deploy a collector or claim end-to-end trace propagation.

### Baseline quality gate

- **R-18**: GitHub Actions runs backend lint, backend type checks, backend unit tests, frontend lint, frontend type checks, and frontend unit tests from locked dependencies.
- **R-19**: CI does not use `continue-on-error`, hidden allow-failure behavior, or repeated reruns to convert failures into success.
- **R-20**: Project-specific directory, testing, configuration, logging, and UI patterns proven by the implementation are written back to `.trellis/spec/` before M0 is archived.
- **R-21**: Request context has a separate, optional principal-enrichment boundary for future authenticated `tenant_id` and `actor_id` data. M0 never fabricates a local tenant or actor; the first tenant-scoped business endpoint in M1 must require a real principal resolver.
- **R-22**: M0 writes a machine-readable evidence manifest under `evidence/m0/`, updates `evidence/index.json`, and records exact validation commands, environment/tool versions, commit SHA, results, artifact paths, limitations, and owner.

## Constraints

- Use Python 3.12, FastAPI, Pydantic v2/Pydantic Settings, SQLAlchemy 2, Alembic, psycopg 3, React, TypeScript, Vite, uv, and pnpm.
- PostgreSQL is the future business source of truth; Redis and MinIO are dependencies only in M0.
- API and Worker code may share infrastructure contracts through `packages/core`, but application entry points and app-specific settings remain owned by their applications.
- Health checks must be deterministic, bounded, and independently unit-testable through injected checkers.
- Local default credentials may exist only in `.env.example` and Compose development configuration. Non-local environments must not silently accept them.
- Exact dependency versions are established by lockfiles during implementation; planning documents must not present unverified versions as installed facts.
- Trellis remains in `planning` until the user explicitly approves these artifacts.

## Non-Goals

- Multipart upload sessions, presigned part URLs, checksums, resume, or upload cleanup.
- Job, Attempt, Event, Outbox, Celery task, claim, lease, heartbeat, fencing, retry, cancellation, or DLQ behavior.
- Document parsing, chunking, embedding, pgvector indexes, retrieval, reranking, citations, or eval datasets.
- LangGraph, MCP, model providers, prompt execution, SSE run streams, approvals, or artifact publication.
- Production authentication, tenant business tables, Row-Level Security, public cloud resources, Kubernetes, staging, image publication, release automation, or rollback drills.
- Production-grade metrics, dashboards, load tests, fault injection, or claims about measured capacity.

## Acceptance Criteria

- [ ] The required monorepo directories, manifests, package boundaries, and lockfiles exist and pass the repository contract test.
- [ ] `uv sync --frozen` and `pnpm install --frozen-lockfile` succeed from a clean checkout with the documented tool versions.
- [ ] `docker compose -f infra/compose/docker-compose.yml up -d --wait` starts healthy PostgreSQL/pgvector, Redis, MinIO, and the one-shot bucket initializer succeeds.
- [ ] Alembic upgrade, downgrade, and re-upgrade succeed against a clean local database; `SELECT extname FROM pg_extension WHERE extname = 'vector'` returns exactly the required M0 extension.
- [ ] API liveness returns 200 without dependency access; API readiness returns 200 with healthy dependencies and 503 with a required dependency unavailable.
- [ ] The Worker starts independently, exposes internal live/ready probes, reports 503 when a required dependency is unavailable, and exits cleanly on termination.
- [ ] The React dashboard renders the real API readiness state and covers loading, healthy, typed-503 degraded, network/schema unreachable, and retry behavior with unit tests.
- [ ] Request and correlation IDs are generated or propagated, returned in headers, and present in structured request logs.
- [ ] Configuration tests prove required-value validation, local/non-local default handling, and secret redaction.
- [ ] An in-memory OpenTelemetry test proves a request and Worker lifecycle span can be emitted when tracing is enabled; tracing-disabled startup and OTLP-exporter construction tests pass without requiring a collector.
- [ ] Backend lint, type checks, and unit tests pass; frontend lint, type checks, and unit tests pass.
- [ ] The GitHub Actions workflow invokes the same locked quality commands and contains no allow-failure path.
- [ ] A documented foundation smoke procedure verifies Compose, migration, API, Worker, and Web startup on a clean local environment.
- [ ] Real conventions discovered during M0 are captured in `.trellis/spec/`; the bootstrap-guidelines task is archived only after that capture is reviewed.
- [ ] `tests/foundation/test_evidence_contract.py` validates the immutable M0 evidence manifest and parent evidence index; every referenced command result and artifact exists for the reviewed commit.
- [ ] No M1-M7 business behavior is represented as implemented or measured.

## Notes

- Parent dependency: this task implements the foundation gate defined by `07-17-enterprise-document-agent-platform`.
- Child dependency: M1 and M2 planning must use the real package, configuration, migration, health, and test contracts produced by M0.
- External cloud accounts, public endpoints, and GPUs are not required for M0.
