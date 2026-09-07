# M5 Observability Evaluation And Load: Design

## Design Summary

M5 adds process-local Prometheus registries to API and Worker processes while retaining
the existing OpenTelemetry trace path. Metrics are explicit dependencies injected at
composition roots. Business logic does not read environment variables and telemetry
export is never a synchronous success dependency.

Evaluation, load, and fault tools write versioned JSON with exact environment and
limitations. Deterministic local results prove contracts and recovery behavior only.

## Signal Contract

API request metrics use `method`, route template, and status class. Worker metrics use
bounded `job_type`, claim result, terminal outcome, and publish result values. Histograms
use seconds. Counters use `_total`. No metric includes UUIDs, principal names, filenames,
query strings, object keys, exception messages, prompts, model output, or tool bodies.

Each process owns one `CollectorRegistry`. Tests and multiple app factories never share
the global Prometheus registry. Celery consumer replicas are scraped as separate targets;
the application does not merge process registries in memory.

## Instrumentation Boundaries

- API middleware: request count and duration after route matching; `/metrics` excluded.
- Worker consumer: claim disposition, elapsed job handling, and bounded final outcome.
- Outbox publisher: claimed, published, failed, poll failure, and publish duration.
- Health endpoints continue to represent dependency readiness, not telemetry health.

## Privacy And Cardinality

Metric label names and values are allowlisted in code. A route template is used after
the downstream application resolves the route. Unknown routes collapse to `unmatched`.
Per-request correlation stays in traces and structured logs, not Prometheus labels.

## Fault Injection

Fault injection is an adapter decoration selected only by a composition root when
`fault_injection.enabled=true`. The setting is rejected outside local/test. Requests
cannot enable or choose faults. Trigger selection is deterministic from configured
target, mode, seed, stable operation identity, and invocation index.

Initial supported targets are handler, model, MCP, multipart object store, and artifact
object store. Modes use existing stable domain error classes where possible. Disabled
configuration instantiates original adapters with no fault branch on their hot path.

## Evaluation And Load Contract

Every report records schema version, scenario, start/end times, environment, workload,
sample count, success/failure totals, error groups, P50/P95/P99, throughput, target,
measured value, and limitations. Percentiles use one documented deterministic method.
One local workstation run is never labeled production capacity.

## Manual Gates

- `m5-real-provider-quality`: requires real provider/model identity, dataset hash,
  sanitized result, latency and cost evidence.
- `m5-representative-capacity-environment`: requires a dedicated production-like host,
  immutable images, resource telemetry, repeated load runs, and raw summaries.
- `m5-managed-observability-validation`: applies only if managed telemetry is required.

These gates are separate from local deterministic and local capacity results.

## Compatibility And Rollback

All runtime changes are additive. Metrics and fault injection can be disabled without a
database rollback. Existing M3/M4 evaluators remain available. Evidence is append-only;
rollback never edits a historical report to make a later run appear successful.

## Reliable Hosted Staging Evaluation

The 2026-09-08 plan is maintained in `docs/ops/NEXT_STAGE_PLAN.md`. The evaluator uses
the public HTTPS control plane and allowlisted object-store endpoints; it does not
need the staging host's Kubernetes or SSH credentials. A prepared dependency environment
did not prevent a later Git checkout failure on that host. Move the manual evaluator
to fixed `ubuntu-24.04` GitHub-hosted jobs while retaining the existing server runtime
and wheelhouse for operator recovery.

`execution_mode` defaults to `validate-only`; `evaluate` is the only live mode.
The `validate` job has no staging Environment or application secrets. It installs
runtime-only frozen dependencies and calls the existing CLI's `--validate-only` path
for both v2 selections. Each run/attempt gets two exact validation report paths under
the runner temporary directory and one validation artifact. The validation suite name
and provenance scope remain distinct from real-provider reports.

The `evaluate` job requires `needs: validate` and the explicit live-mode condition.
It uses a separate fresh hosted VM, the same fixed Python/uv Actions as normal CI, and
the staging Environment. Its only token-bearing step receives the dedicated smoke
token and host allowlists; checkout/setup do not receive application credentials.
Use a one-commit checkout without persistent Git credentials: report provenance reads
HEAD and dirty state only. Keep default checkout cleanup, frozen runtime-only sync,
no implicit sync in evaluation, five-minute dependency setup, the forty-minute live
job limit, the 1800-second evaluator window, and shared staging concurrency.

Retain the current quality artifact name/path and always-upload behavior in the live
job so failed reports remain inspectable. Validation output must never be downloaded
or indexed as real quality evidence. The current thresholds, synthetic corpus,
model routing and deployed images are unchanged. A real hosted preflight and the first
authorized trial are required to establish network/runtime behavior; local tests alone
do not establish it. Publication and new live calls use a concrete reviewed action list.

Rollback restores the previous workflow revision and separately prepared server runtime;
it does not delete evidence, reset application data, or remove the existing wheelhouse.
