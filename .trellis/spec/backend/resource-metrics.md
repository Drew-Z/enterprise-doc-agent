# Worker resource observations

## 1. Scope / Trigger

Use for queue/Redis capacity observations. Core owns reusable read functions and
metrics; the existing Worker owns scheduling. API/Consumer do not scan these
shared resources. This is a code candidate, not evidence of a deployed producer,
business capacity or formal release. There is no schema migration or product API.

## 2. Signatures

- `jobs.metrics.read_queue_oldest_age(async_sessionmaker[AsyncSession]) -> float`
- `telemetry.resources.read_redis_connected_clients(Redis) -> float`
- `ResourceMetricsSampler(metrics, queue_reader, redis_reader, *, clock=time.time,
  interval_seconds=10, timeout_seconds=2).sample_once()` / `.run(shutdown_event)`
- `MetricsRuntime.record_resource_observation(source, value, *, observed_at)`
- `supervise_worker_tasks(..., resource_observer=None)` owns cancellation/join.

## 3. Contracts

Queue age is elapsed database time since the oldest **due** `available_at` among
`pending`/`retry_wait` durable jobs, across ingestion/Agent/Presales and tenants.
Select the first timestamp separately for each status using the existing
`ix_jobs_claimable` prefix. Future backoff, running jobs and terminal jobs are
excluded. A successfully observed empty set is zero; a query failure is unknown.
This is not end-to-end age since submission, Celery message depth, or an expired
running-lease indicator. No job content or tenant identifiers are read/exported.

Use a fresh read-only transaction with a local 1,500 ms statement timeout and
the sampler's two-second source deadline. No row locks or business mutations.
Redis uses only `INFO clients` and requires a nonnegative integer
`connected_clients`; never enumerate client identities or queue message bodies.
The new `enterprise_doc_redis_connected_clients` measures the **server**, not the
current process pool. Legacy `enterprise_doc_redis_connections` is retained with
its old meaning and initializes as NaN; do not feed it the server count.

`enterprise_doc_queue_oldest_age_seconds` and the new Redis gauge initialize as
NaN. Two bounded labels (`source="queue"|"redis"`) accompany:

- `enterprise_doc_resource_sample_success`: latest completed observation, 0/1.
- `enterprise_doc_resource_last_success_timestamp_seconds`: Unix timestamp,
  initially zero; preserved on failure.

Publish success=0 before updating separate fields, then value/time and success=1
last. A failure, timeout, cancellation, invalid value or invalid clock publishes
NaN/success=0 and retains last success time. The legacy queue setter alone does
not establish freshness. No raw exceptions, SQL, endpoints or Redis response
bodies are logged. Reads run independently under TaskGroup and cancellation drains
both. Ordinary source failures do not stop business tasks. An unexpected observer
exit follows the existing Worker supervision failure policy.

Worker starts the observer only with `otel.metrics_enabled`; it reuses existing
sessions/Redis and waits ten seconds after completion, without catch-up or overlap.
Normal shutdown cancels the observer before shared clients close. No new daemon.

The read-only telemetry parser accepts only the two source labels, retains missing
or nonfinite fields as null, and rejects duplicate series or unexpected labels.
Every observed Worker replica must supply both resources with success=1, finite
values and last-success time within 45 seconds, at most five seconds in the future,
and not before process start. API/Consumer resource NaNs are not missing Worker
evidence; their process/DB-pool gauges remain required. Old producers keep the
`queue_and_redis_producer_freshness_unverified` issue. Reports list unresolved gauges
and source-specific reasons; `read_only_observed` never approves production capacity.
The Prometheus capacity example also gates Worker values on success and freshness.

## 4. Validation & Error Matrix

| Input or event | Result |
| --- | --- |
| Empty due queue with successful query | zero, successful timestamp |
| Future retry / running / terminal work | excluded from due waiting age |
| DB unavailable / blocked beyond deadline | queue NaN, timestamp unchanged; Redis independent |
| Missing/bool/string/negative Redis count | rejected; no invented zero |
| Redis connection failure then recovery | NaN/failed then real count/new success time |
| Collector cancellation | both reads drained; interrupted values unknown |
| Legacy/missing, stale, future or pre-start timestamp | incomplete read-only report |
| Fresh Worker data | resource gap resolved; capacity approval remains false |

## 5. Good / Base / Bad Cases

Good: a real isolated PostgreSQL queue drains, Redis clients increase/decrease,
failed connections and a locked table yield unknowns, and recovery restores values.
Base: a candidate passes local tests while the live old producer stays unverified.
Bad: interpret registry default zero as idle, use Redis server count as process
pool size, or remove old failed evidence after upgrading the parser.

## 6. Tests Required

- Core public sampler/renderer: initial unknowns, independent failures, retained
  success time, timeout, invalid values, cancellation and Redis payload validation.
- Real local DB/Redis: exclusively owned schema using current metadata/migration,
  due/future/running/terminal jobs, no query mutations, drain, connection changes,
  actual unavailable loopback socket, table-lock timeout and recovery. Cleanup
  removes only fixture schema and owned clients; shared services keep running.
- Registry text -> parser -> summary: fresh/legacy/failed/stale/future/restarted
  cases and Worker ownership. No model, embedding or email calls.
- Worker supervision, full non-integration regression, Ruff/format and strict Mypy.

## 7. Wrong vs Correct

Wrong: change a permanently incomplete report into success whenever a queue gauge
contains zero. Correct: require an actual successful, recent sample from every
Worker, preserve unknowns/history and separate observational completeness from
business capacity and deployment acceptance.

## Proven Examples

- `packages/core/src/enterprise_doc_core/jobs/metrics.py`
- `packages/core/src/enterprise_doc_core/telemetry/resources.py`
- `apps/worker/src/enterprise_doc_worker/__main__.py`
- `tests/jobs/test_resource_metrics_integration.py`
- `tests/deployment/test_business_telemetry.py`
