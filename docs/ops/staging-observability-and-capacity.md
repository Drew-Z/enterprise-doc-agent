# Staging Observability And Capacity

This document records the non-production observability and bounded load checks for
the single-node staging deployment. It does not promote the environment to a
production SLO or capacity claim.

## Readiness behavior

`GET /health/live` is a process-only liveness check and never contacts a dependency.
`GET /health/ready` checks PostgreSQL, Redis, and both object-store buckets. The API
uses `API__READINESS_CACHE_TTL_SECONDS` (2 seconds in the reviewed Kubernetes base
ConfigMap) to prevent readiness probes and load bursts from multiplying external
dependency checks:

- the first request performs the real checks;
- requests inside the TTL reuse the latest result;
- after expiry, one request refreshes while concurrent requests receive the latest
  bounded result;
- a failed refresh is cached as `not_ready`, so an outage is not hidden indefinitely;
- set the value to `0` for local tests that require an uncached check on every request.

This cache is an availability/readiness control, not a replacement for dependency
metrics or alerting. Kubernetes liveness remains independent.

## Internal metrics

Metrics are not exposed through the public Ingress. The API and Worker expose
Prometheus text on their internal Services, and the Consumer exposes the same
registry on its metrics port. For an operator-only inspection:

```bash
sudo kubectl -n enterprise-doc-agent-staging port-forward \
  svc/enterprise-doc-api 19000:8000
curl --fail http://127.0.0.1:19000/metrics
```

Use an equivalent temporary forward for `enterprise-doc-worker:8081` and
`enterprise-doc-consumer:8082`; terminate the forward after inspection. Do not add
the metrics paths to the public Ingress or copy raw metric output into an evidence
artifact if it contains environment-specific labels.

The bounded labels include API route templates/status classes, known job types,
dependency names, queue/publish results, and business boundary outcomes. Tenant,
document, run, user, object-key, prompt, and error text are not metric labels.

## Bounded staging load

The following command exercises only the public readiness endpoint and is useful for
detecting regressions in the control-plane path:

```powershell
uv run python scripts/load_m5.py `
  --scenario ready `
  --base-url https://agent.playlab.eu.cc `
  --requests 200 `
  --concurrency 20 `
  --request-timeout-seconds 15 `
  --report-path C:\path\outside\repo\staging-ready-load.json
```

The report must remain labelled as a bounded run unless it includes repeated ramp,
steady-state, burst, and recovery phases, immutable image identity, dependency
telemetry, and an isolated production-like environment. A non-zero error rate or a
latency regression is a diagnostic signal, not evidence to suppress by raising the
target.

## Current observation

Before the readiness cache was added, a 200-request/20-concurrency run completed all
requests but measured approximately 9.57 requests/second, P50 1.85 seconds, P95
3.86 seconds, and P99 4.32 seconds. The run had no host or dependency sampler and was
therefore recorded as a failed bounded baseline, not a production capacity result.

After the next immutable staging rollout, repeat the same command and compare the
latency distribution. Also inspect API `enterprise_doc_api_request_duration_seconds`
and dependency duration histograms through the internal metrics forward. Keep both
before/after reports outside the repository unless they are intentionally sanitized.

## Escalation boundaries

- A readiness failure blocks rollout and requires dependency diagnosis.
- A high readiness latency with healthy dependencies should first be checked against
  cache age, object-store latency, and database pool utilization.
- Real upload -> ingestion -> retrieval -> Agent validation remains the authoritative
  business-path smoke; the readiness load does not replace it.
- Managed retention, alert routing, backup/restore, and representative capacity remain
  external gates and are not claimed by this document.
