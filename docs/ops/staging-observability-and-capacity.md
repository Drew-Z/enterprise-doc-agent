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

## v0.1.18 observation

Before the readiness cache was added, a 200-request/20-concurrency run completed all
requests but measured approximately 9.57 requests/second, P50 1.85 seconds, P95
3.86 seconds, and P99 4.32 seconds. The run had no host or dependency sampler and was
therefore recorded as a failed bounded baseline, not a production capacity result.

Release `v0.1.18` deployed the cache through staging run
`https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/30964373803`. Migration,
workload rollout, the real embedding/reindex gate, in-cluster readiness smoke, and the
authenticated upload -> ingestion -> Agent smoke all passed on attempt 3. The first two
public readiness repetitions exposed a separate network boundary:

- QUIC run 1: 190/200 successful, P50 1.29 seconds, P95 13.77 seconds, and 10 transport
  timeouts;
- QUIC run 2: 175/200 successful, P50 1.59 seconds, P95 15.01 seconds, and 25 transport
  timeouts;
- an operator-only Pod forward completed 200/200 at approximately 389.82 requests/second,
  P50 22.86 milliseconds, P95 35.70 milliseconds, and P99 40.26 milliseconds;
- the host-to-loopback Traefik path completed 200/200 at approximately 160.36
  requests/second, P50 47.01 milliseconds, P95 82.79 milliseconds, and P99 111.94
  milliseconds.

API logs showed sub-millisecond cached handlers and no application request over 1.52
seconds in the first public failure window. Some timed-out requests arrived late or never
reached the API. `cloudflared` exposed four QUIC connections to LAX with roughly 416-471
millisecond smoothed RTT and timeout packet loss. This isolated the dominant tail to the
Tunnel/edge path rather than Kubernetes, Traefik, API CPU/memory, or dependency probes.

The reviewed host drop-in in
`infra/host/ubuntu-24.04/systemd/cloudflared.service.d/transport.conf` changed only the
Tunnel transport to HTTP/2. Two repetitions then completed 200/200 with no transport
errors:

- HTTP/2 run 1: 13.65 requests/second, P50 1.20 seconds, P95 2.54 seconds, and P99 2.86
  seconds;
- HTTP/2 run 2: 16.25 requests/second, P50 1.01 seconds, P95 1.83 seconds, and P99 2.44
  seconds.

Both reports still have `status: failed` because their public P95 exceeds the script's
250-millisecond local target. Preserve that result: the change removes errors and improves
the bounded baseline, but it does not establish a public SLO or production capacity. The
raw reports remain outside the repository under the operator's temporary evidence path.

## Escalation boundaries

- A readiness failure blocks rollout and requires dependency diagnosis.
- A high readiness latency with healthy dependencies should first be checked against
  cache age, object-store latency, and database pool utilization.
- Real upload -> ingestion -> retrieval -> Agent validation remains the authoritative
  business-path smoke; the readiness load does not replace it.
- Managed retention, alert routing, backup/restore, and representative capacity remain
  external gates and are not claimed by this document.
