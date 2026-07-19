# Enterprise Document Agent Platform

This repository is a modular monorepo for a tenant-scoped enterprise document Agent
platform. M1-M7 is the repository milestone envelope. M1 provides resumable direct-to-object-store upload, M2 provides durable
Job/Attempt/Outbox execution, and M3 provides deterministic parsing plus PostgreSQL
FTS/pgvector hybrid retrieval.

M4 adds a controlled local Agent workflow: a fixed LangGraph graph with the official
PostgreSQL checkpointer, deterministic and OpenAI-compatible model gateways, strict
grounded citation validation, a five-tool MCP stdio server, exact-target owner approval,
ordered SSE replay, verified artifacts, and a typed React run workspace. The local
deterministic provider proves orchestration and policy contracts. It is not production
model-quality, public MCP, capacity, Kubernetes, or deployment evidence.

M5 now adds process-local Prometheus metrics, local/test-only deterministic fault
injection, a unified RAG/Agent evaluation report, and a bounded HTTP load runner. M6
adds non-root service Dockerfiles, Kubernetes base/staging/prod manifests, migration,
probe, RBAC/NetworkPolicy/PDB contracts, supply-chain workflows, and guarded backup/
restore/rollback scripts. M7 adds provider route metadata, retryable-only fallback,
CLOSED/OPEN/HALF_OPEN circuit breaking, a shared primary/fallback route deadline,
embedding-dimension checks, and deterministic
benchmark reports. These are implementation and local-contract facts. Real registry,
cluster, staging, production-capacity, managed-observability, and GPU/vLLM evidence
remain open external gates.

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

The root Python project depends on the API, MCP, Worker, and Core workspace packages,
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

Initialize and verify the official LangGraph PostgreSQL checkpoint schema:

```powershell
uv run enterprise-doc-checkpointer-setup --setup
uv run enterprise-doc-checkpointer-setup --check
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

The Agent Worker launches `enterprise-doc-mcp --stdio` as an internal subprocess with
a signed, short-lived execution context. Running `uv run enterprise-doc-mcp --stdio`
directly is intended for protocol diagnostics; stdout is reserved for MCP frames and
operational JSON logs go to stderr.

The local endpoints are:

- API live: `http://127.0.0.1:8000/health/live`
- API ready: `http://127.0.0.1:8000/health/ready`
- Worker live: `http://127.0.0.1:8081/health/live`
- Worker ready: `http://127.0.0.1:8081/health/ready`
- API metrics: `http://127.0.0.1:8000/metrics`
- Worker metrics: `http://127.0.0.1:8081/metrics`
- Consumer metrics: `http://127.0.0.1:8082/metrics`
- Web dashboard: `http://127.0.0.1:5173`

The Web app stores the local bearer token only in session storage. Agent SSE uses an
authenticated fetch stream and `Last-Event-ID`; it does not place the token in a URL.
Only `{version, runId, lastSequence}` is persisted for run recovery. Artifact downloads
always request a fresh short-lived URL after database/object metadata verification.

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
uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
uv run pytest -m "not integration"
```

The individual frontend commands are:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm --filter web test:e2e
```

Run the M4 deterministic safety contract:

```powershell
uv run python scripts/evaluate_m4_agent.py
uv run pytest tests/security tests/contracts -q
```

Run M5/M7 local evaluation and bounded load contracts:

```powershell
uv run python scripts/evaluate_m5.py
uv run python scripts/load_m5.py --scenario health --requests 20 --concurrency 4
uv run python scripts/load_m5.py --scenario ready --requests 1000 --concurrency 20 --sample-resources --resource-sample-interval-seconds 0.1 --report-path evidence/m5/20260719-m5-local-ready-resource-load.json
uv run python scripts/benchmark_m7.py --scenario deterministic --iterations 20
uv run python scripts/benchmark_m7.py --scenario fallback-contract --iterations 20
```

Authenticated load scenarios read the bearer token from
`ENTERPRISE_DOC_LOAD_TOKEN`; the token is not accepted as a command-line argument.
`agent-create`, `duplicate-agent-create`, and `end-to-end` also require an authorized
`--document-version-id`. Every report separates targets from measured values and states
that a bounded local run is not production capacity.

Fault injection is disabled by default and rejected outside local/test. It is selected
only through `FAULT_INJECTION__*` process settings at the Worker composition root; API
requests cannot turn it on. Supported boundaries include handler, model, MCP, and
multipart object-store operations.

Run the optional local Prometheus/Grafana profile while the API, Worker probe, and
consumer are running on the host:

```powershell
docker compose -f infra/compose/docker-compose.yml --profile observability up -d
```

Prometheus is available at `http://127.0.0.1:9090` and Grafana at
`http://127.0.0.1:3000`. The dashboard only uses bounded process metrics; the profile is
local-only and telemetry failure does not gate business requests.

Plan or run a guarded local dependency outage drill. The command refuses staging and
production environments and requires an explicit confirmation for execution:

```powershell
uv run python scripts/fault_drill.py --scenario redis --plan
uv run python scripts/fault_drill.py --scenario minio --plan
uv run python scripts/fault_drill.py --scenario redis --run --confirm local-fault-drill --report-path tmp/redis-drill.json
```

The worker lease drill remains an operator procedure because killing an unspecified
consumer would be unsafe. Use `--plan`, hard-kill the active consumer, wait beyond its
lease, then verify the attempt history and fencing fields in PostgreSQL. Redis recovery
must allow the Outbox publishing lease to expire before expecting a republish. The
automated Redis/MinIO drill itself is readiness-only: it does not execute Outbox
republish verification or MinIO object-content reconciliation.

Render the Kubernetes contracts and inspect safe release tooling:

```powershell
kubectl kustomize infra/k8s/base
kubectl kustomize infra/k8s/overlays/staging
uv run python scripts/backup_database.py --help
uv run python scripts/restore_database.py --help
uv run python scripts/rollback_release.py --reason validation-only --revision enterprise-doc-api=1
```

Rollback validation requires an explicit positive Deployment revision for each target
(or a JSON revision map in `ROLLBACK_REVISIONS_JSON`); it does not infer an implicit
“previous” revision. `--migration-revision` is recorded separately and is not a
replacement for the Deployment revision.

The restore and rollback scripts are dry-run/validation paths unless `--confirm` is
provided. Actual registry push, digest promotion, cluster apply, TLS/secret-manager
review, backup restore and rollback drills require external credentials and immutable
evidence. The staging workflow now has an authenticated main-path smoke that performs
upload -> direct object PUT -> ingestion-ready -> Agent run; it requires a dedicated
staging token, an externally reachable API base URL, and a presign endpoint reachable
from the runner.

Recovery and capacity reports use one deterministic contract before they can be treated
as gate evidence. Validate a report and its repository-relative artifact hashes with:

```powershell
uv run python scripts/validate_recovery_capacity_evidence.py --input evidence/delivery/recovery-report.json --root .
```

Executed reports must identify the external environment and cluster, reviewed commit,
immutable image digest(s), operator, timezone-aware time bounds, measured results and
artifact SHA-256 values. Recovery reports additionally require backup/restore/rollback
timings and data/application smoke checks. Application-capacity reports require
ramp/steady/burst/recovery repetitions, latency percentiles, errors, throughput and
dependency telemetry; model-capacity reports require warm-up, TTFT/TPOT, token
throughput, GPU/KV-cache telemetry and headroom. When the required external target or
measurements do not exist, the report must be `blocked_external` with a blocking reason
and prerequisites; a blocked capacity record still names its planned profile, phases
and repetition count. A local workstation run cannot be promoted to `passed`.

`evidence/m4/20260719-153214-m4-agent-mcp-hitl.json` is the formal M4 status summary and
is explicitly `blocked_external`. It links the original
`evidence/m4/20260719-075820-m4-agent-mcp-hitl-working-tree.json` capture and the
`m4-reviewed-immutable-evidence` gate. The capture records sanitized local verification
and SHA-256 values, but its reviewed implementation commit and evidence commit are
intentionally null; it must not be promoted to `passed` until the gate is closed. The
separate `evidence/index.json` working-tree entry points to the latest unreviewed refresh
without rewriting that historical formal manifest.

M5/M6/M7 local-only evidence is kept separate from reviewed evidence. Their formal
summaries are `blocked_external` and link the raw working-tree captures plus individual
gate records under `evidence/gates/`:

- `evidence/m5/20260719-m5-unified-evaluation.json` and
  `evidence/m5/20260719-m5-local-health-load.json` record deterministic evaluation and
  the earlier 100-request/concurrency-10 health baseline. The newer
  `evidence/m5/20260719-m5-local-ready-resource-load.json` records a 1000-request,
  concurrency-20 dependency-inclusive ready run with 43 host/API-process resource
  samples. Both are single bounded workstation runs, not production capacity or SLO
  evidence. The M5 manifest also indexes local Redis/MinIO outage-recovery reports and
  a Prometheus/Grafana profile provisioning check; these are not managed-service
  failover, production RTO, or production observability evidence.
- `evidence/m7/` records deterministic and fallback-contract route benchmarks. The
  fallback contract checks retryable routing, fallback count, breaker state and local
  citation validity; it is not real-provider quality, cost, GPU, vLLM, or
  production-capacity evidence.
- `evidence/m6/` records local Docker image builds plus Docker/Kubernetes/workflow contracts; registry signing,
  cluster rollout, staging smoke, backup/restore, and rollback remain external gates.

GitHub Actions runs independent backend and frontend jobs from `uv.lock` and
`pnpm-lock.yaml`. The `m1-integration` job starts real PostgreSQL and MinIO, runs the
multipart integration suite, then executes a two-part restart/resume smoke.
`m4-integration` runs the full marked integration suite with PostgreSQL/Redis/MinIO,
checkpoint setup, and the M4 safety command. `web-e2e` installs Chromium and runs the
upload-recovery and Agent approval/download workflows.
The smaller CI payload is a fast regression gate and does not replace the required
local 1 GiB evidence run. No job has an allow-failure or retry-to-green path.

## Repository Boundaries

- `apps/api`: FastAPI routes and request middleware
- `apps/mcp`: stable v1 MCP stdio server and protocol adapter
- `apps/worker`: long-running Worker lifecycle and internal probes
- `apps/web`: React upload and Agent run workspaces
- `packages/core`: shared platform, document, Job, Agent, approval, tool, and artifact contracts
- `infra/compose`: PostgreSQL/pgvector, Redis, MinIO, bucket initialization
- `infra/docker`: non-root API, Worker, consumer, and Web image definitions
- `infra/k8s`: base and environment overlays with migration/probe/security contracts
- `tests/foundation`: repository, migration, runtime, CI, documentation, and M0 evidence contracts
- `tests/multipart`: M1 unit, API, PostgreSQL/MinIO, recovery, cleanup, and evidence contracts
- `tests/agent`: run, graph, checkpoint, SSE, approval, Worker, and recovery integration
- `tests/mcp`: PostgreSQL/MinIO and stdio tool-policy integration
- `tests/security` and `tests/contracts`: injection, authorization, and M4 evaluation contracts

The API and Worker may depend on Core. They do not import each other. Web uses the
API control plane while document bytes travel directly to the configured object store.

## Known Production Gaps

- No production semantic embedding or real chat-model quality benchmark is claimed.
- MCP is local stdio, not a public authenticated remote MCP deployment.
- Tenant membership is the authorization boundary; per-document ACL/ABAC is not implemented.
- Local Kubernetes manifests and CI/CD workflows exist, but no real registry digest,
  cloud-cluster rollout, TLS/secret-manager review, authenticated staging smoke run,
  production QPS, multi-region recovery, backup-restore RTO/RPO, promotion, or rollback
  evidence exists. The workflow definitions include these gates but their remote
  execution is not implied by local static checks.
- No GPU/vLLM/quantization throughput or memory result is claimed; M7 reports only the
  deterministic local routing and fallback contract until hardware evidence exists.
- The deterministic safety corpus is a repeatable regression set, not complete adversarial certification.
