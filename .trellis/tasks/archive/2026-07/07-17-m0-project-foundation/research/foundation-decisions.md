# M0 Foundation Decisions

## Verified planning basis

- The parent PRD assigns M0 repository structure, local dependencies, health, configuration, baseline CI, and observability.
- The parent design fixes PostgreSQL as the future business source of truth, Redis as coordination only, MinIO/S3 as object storage, and a modular monorepo as the initial architecture.
- The source implementation plan requires Python 3.12/FastAPI, React/TypeScript, PostgreSQL/pgvector, Redis, MinIO, uv, pnpm, live/ready probes, an initial migration, request IDs, structured logs, and initial OpenTelemetry.
- The Trellis workflow is TDD and requires requirements, design, ordered implementation slices, validation commands, review gates, and rollback points before implementation begins.

## Locked M0 decisions

1. M0 creates `apps/web`, `apps/api`, `apps/worker`, `packages/core`, `infra`, and `tests`.
2. API and Worker are separate processes; Core contains only shared foundation contracts.
3. Compose runs PostgreSQL/pgvector, Redis, MinIO, and bucket initialization, not application images.
4. The initial Alembic revision enables exactly the PostgreSQL `vector` extension and creates no M1-M7 business tables.
5. API and Worker have independent live/ready probes with shared injectable dependency-check contracts and the exact `ready/not_ready` plus `up/down/timeout` response model.
6. The Web first screen is a real operational readiness dashboard and contains no fake jobs/documents.
7. M0 proves only OTel SDK/bootstrap, in-memory spans, and OTLP exporter construction; propagation, collectors, dashboards, SLOs, load tests, and fault injection remain M5.
8. CI is limited to locked baseline quality checks; images, security scans, Kind, deployment, and rollback remain M6.
9. Trellis package mappings are added only after the corresponding directories exist.
10. Implementation cannot start until the user explicitly approves the M0 planning artifacts.
11. M0 never fabricates tenant or actor identities; M1 must add a real principal resolver before the first tenant-scoped business endpoint.
12. M0 completion produces an immutable manifest under `evidence/m0/` and updates `evidence/index.json` according to the parent evidence contract.

## Explicit exclusions

Multipart upload, durable jobs, Celery processing, lease/heartbeat/fencing, RAG, Agent execution, MCP, SSE, approval, Kubernetes, staging, production release, and performance claims are not M0 deliverables.
