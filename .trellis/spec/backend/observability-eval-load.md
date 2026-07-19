# Observability, Evaluation And Load

## Adopted Facts

- `MetricsRuntime` owns one explicit Prometheus `CollectorRegistry` per process.
- API labels are bounded to method, route template and status class.
- Worker labels are bounded to known job type, claim/outcome, heartbeat and publish result.
- Readiness and model gateway wrappers emit bounded dependency duration/up metrics;
  database-pool, queue-age and Redis-connection gauges are explicit measured inputs and
  are never populated with invented defaults.
- Request, tenant, run, job, event, filename, object key, prompt and error text are not
  Prometheus labels.
- Fault injection is disabled by default, allowed only in local/test and selected only
  at Worker composition roots.
- `scripts/evaluate_m5.py` records dataset hashes, behavior versions, target and measured
  values; deterministic results are not real-provider quality.
- `scripts/load_m5.py` reports nearest-rank P50/P95/P99 and explicitly labels one bounded
  run as non-production capacity.
- `scripts/run_application_capacity.py` executes repeated ramp, steady-state, burst and
  recovery phases, preserves raw request samples and aggregate Prometheus snapshots, and
  cannot label a local-only run `passed`.
- The optional Compose profile includes an OTLP Collector with principal/request
  attribute deletion plus Prometheus recording and alert rules. Kubernetes leaves OTLP
  disabled until an actual collector endpoint is reviewed.

## Proven Examples

- `apps/api/tests/test_metrics.py` and `packages/core/tests/test_metrics.py` verify
  process-local registries and bounded metric labels.
- `tests/evaluation/test_fault_drill.py` proves that fault drills are local/test-only,
  sanitize reports and refuse staging or production targets.
- `infra/observability/` provisions Prometheus scrape targets and Grafana dashboards for
  API RED, Worker, consumer, outbox, heartbeat and dependency signals, plus local OTLP
  receipt and alert-rule evaluation.
- `tests/deployment/test_run_application_capacity.py` proves the repeated matrix can
  produce a validator-accepted report only when dependency telemetry is present.
- `evidence/m5/20260719-redis-outage-recovery.json` and the MinIO companion report record
  guarded local readiness loss/recovery runs without claiming production failover.
- `evidence/m5/20260719-observability-stack-runtime.json` records successful local profile
  startup and dashboard provisioning while explicitly retaining down target status.
- `evidence/m5/20260719-m5-unified-evaluation.json`, the 100-request health baseline,
  and `evidence/m5/20260719-m5-local-ready-resource-load.json` are local evidence. The
  ready run uses 1000 requests at concurrency 20 with host and selected API-process
  resource sampling; neither run is representative production-quality or
  production-capacity evidence.

## Proven Files

- `packages/core/src/enterprise_doc_core/telemetry/metrics.py`
- `apps/api/src/enterprise_doc_api/middleware/metrics.py`
- `apps/worker/src/enterprise_doc_worker/faults.py`
- `packages/core/src/enterprise_doc_core/evaluation/contracts.py`
- `tests/evaluation/test_m5_load.py`
- `scripts/run_application_capacity.py`
- `infra/capacity/application-capacity.example.yaml`
- `infra/observability/otel-collector-config.yaml`
- `infra/observability/rules/enterprise-doc-agent.rules.yml`
