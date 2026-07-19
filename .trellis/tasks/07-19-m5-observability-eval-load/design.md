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
