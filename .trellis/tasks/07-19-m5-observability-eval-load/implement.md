# M5 Observability Evaluation And Load: TDD Implementation Plan

## Preconditions

- M0-M3 are archived and M4 implementation remains green in the current working tree.
- M5 work must not convert M4 working-tree evidence into reviewed evidence.
- Local deterministic results and workstation load results are labeled with their exact
  scope and never promoted to production claims.

## Slice 0: Measurement Contracts And Trellis Task

- [x] Add M5 PRD, design, implementation plan, task metadata, and manual-gate rules.
- [x] Define metrics, load report, fault result, and evaluation result schemas.
- [x] Add redaction, finite-label, percentile, and report validation tests.

Rollback point: contracts and tests only; no runtime behavior changes.

## Slice 1: API And Worker Prometheus Metrics

- [x] Add an explicit process-local metrics registry and typed collectors.
- [x] Expose Prometheus-compatible `/metrics` from API and Worker probe apps.
- [x] Instrument API route-template latency/error, Worker job outcomes, and Outbox
  publication with bounded labels.
- [x] Prove repeated app factories do not produce duplicate collectors and metrics never
  contain raw identifiers, secrets, signed URLs, or bodies.

Rollback point: removing metrics injection leaves business behavior unchanged.

## Slice 2: Deterministic Fault Injection

- [x] Add default-off local/test-only settings and validation.
- [x] Add deterministic handler, model, MCP, and object-store adapter decorators.
- [x] Add retry, terminal, lease, and zero-duplicate-side-effect tests.

Rollback point: disabled configuration builds the original adapters.

## Slice 3: Unified Evaluation Contract

- [x] Add M5 dataset and report schemas that index M3 retrieval and M4 safety/Agent runs.
- [x] Report dataset and behavior-version hashes, aggregate quality, zero-tolerance
  safety results, and explicit deterministic-provider limitations.
- [x] Keep real-provider evaluation as a separate external manual gate.

Rollback point: M3 and M4 evaluator entry points remain unchanged.

## Slice 4: Reproducible Local Load Runner

- [x] Add bounded-concurrency health, create/status, duplicate-idempotency, and optional
  end-to-end scenarios using real HTTP endpoints.
- [x] Emit deterministic JSON/Markdown with P50/P95/P99, errors, throughput, environment,
  sample count, saturation inputs, bottleneck, and capacity conclusion.
- [x] Add offline unit tests for percentile and report contracts.

Rollback point: load tooling is additive and test data is explicitly cleanable.

## Slice 5: Fault And Recovery Procedures

- [x] Record local procedures for worker termination/lease reclaim, Redis outage,
  model timeout, and MinIO failure/recovery; the automated Redis/MinIO drill is explicitly
  readiness-only and does not claim Outbox republish or object-content reconciliation.
- [x] Assert authoritative PostgreSQL state and no duplicate effective side effects through the
  existing Job/Agent integration contracts and explicit worker-lease procedure.
- [x] Preserve unavailable representative infrastructure as `blocked_external`.

Rollback point: procedures are local/test-only and include cleanup.

## Slice 6: Local Observability Stack

- [x] Add version-pinned Prometheus and Grafana configuration under an optional Compose profile.
- [x] Add a dashboard for API RED, Outbox, Worker jobs, retries, and dependency health.
- [x] Verify telemetry outage is fail-open and document process-scrape topology.

Rollback point: observability services are optional and independent of readiness.

## Slice 7: Evidence And Documentation

- [x] Run the available focused and regression gates, evaluators, the 100-request health
  baseline, the 1000-request dependency-inclusive ready load with host/API resource
  sampling, guarded
  Redis/MinIO outage-recovery drills, and observability profile provisioning checks;
  production failover and RTO/RPO remain external gates.
- [x] Save sanitized artifacts and SHA-256 hashes under `evidence/m5/`.
- [x] Add working-tree manifests, manual gates, evidence index entries, README/spec updates,
  and code-backed interview Q&A.
- [x] Separate reviewed implementation and evidence commits when the user approves the
  commit boundary.

## Slice 8: Protected Staging Quality Execution

- [x] Add a manual, Environment-protected `trial`/`full` v2 RAG evaluation workflow that
  serializes with staging deployment and rollback, has no Kubernetes credentials, and
  uploads only the sealed report.
- [ ] Dispatch a clean full 40-case execution only after stable provider revision,
  billing metadata, approved representative corpus scope, and independent human semantic
  reviewer are available. A workflow pass alone is not a completion claim.

## Full Completion Gate

```powershell
uv sync --frozen
pnpm quality
uv run pytest -m integration -q
pnpm --filter web exec playwright test
uv run python scripts/evaluate_m3_retrieval.py
uv run python scripts/evaluate_m4_agent.py
uv run python scripts/evaluate_m5.py
uv run python scripts/load_m5.py --help
uv run python .trellis/scripts/task.py validate 07-19-m5-observability-eval-load
git diff --check
```

## Completion Rules

- A target is not a measured result.
- A local workstation result is not production capacity.
- Deterministic providers prove contracts and policy, not real-model answer quality.
- `blocked_external` requires a stable manual-gate record and does not satisfy a passed
  parent requirement.
- No M5 task is archived from uncommitted working-tree evidence alone.
