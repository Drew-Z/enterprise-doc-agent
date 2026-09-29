# Cloudflare external readiness monitor

## Ownership and entry points

`infra/observability/monitor.ts` runs independently of the application node. Its
public boundaries are `probeReadiness`, `recordObservation`, `deliverNotification`,
`runScheduled` and the default Worker handler. D1 schema and Wrangler configuration
live beside it. Keep the readiness wire contract aligned with
`scripts/check_external_readiness.py`; reuse existing `/health/ready`, not a new
public operations endpoint.

## Observable contract

Probe: one HTTPS GET, manual redirects, no credentials or automatic retry, 10-second
total deadline including streamed body, at most 64 KiB. Require JSON, all known and
additional checks up, an explicit valid timezone-aware timestamp, age at most 120
seconds, future skew at most 15 seconds. Never retain raw response or error bodies.

Scheduling: minute-aligned key, reject duplicate/out-of-order/expired samples;
missing minute resets the streak. Three failures open an incident, two successes
close it. Initial health is silent. D1 `batch` atomically commits counters, unique
event and incident transition. Readiness state is not inferred from missing data.

Delivery: commit `attempting` before the Email binding. Only `pending` can be
claimed; pending events survive interruption and drain at most two per tick.
`unknown` or abandoned `attempting` must never automatically retry. `accepted`
requires a provider message ID, but remains distinct from inbox receipt. `observe`
records suppressed events. Fixed environment addresses must match both binding
allowlists. No arbitrary-recipient or HTTP administration API.

## Failure and acceptance boundaries

| Condition | Result |
|---|---|
| Body stalls after HTTP headers | Timeout; never counted healthy |
| Parallel or late cron | At most one sample/event for that minute |
| Failure after event commit, before dispatch | Pending event claimed on next invocation |
| Failure after email attempt starts | Unknown/attempting retained; no automatic duplicate |
| Unverified recipient | No notify deployment until verified |
| Synthetic drill | Separate key and Worker, five fixed minutes, at most two labelled events |
| Public target unhealthy | Real readiness evidence; no injection into application workloads |

A new Cron Trigger may take up to 15 minutes to propagate. Freeze drill start after
that window before first deployment; do not reschedule a failed trial and erase
missing samples. User authorization covers exact verification/test recipients and
counts. Daily delivery activates only after the user confirms the test receipts.
The original mailbox, routing/DNS and application resources remain separate.

## Tests and runtime details

Node tests use real Miniflare/workerd D1; substitute only target HTTP, email and
clock boundaries. Execute the shipped scheduled handler in workerd as well as
direct module behavior. Global `fetch` must be wrapped when injected into an object:
`(url, options) => fetch(url, options)`; passing it as a method caused an illegal
receiver in workerd while Node-only tests passed.

Pin Wrangler and its matching Miniflare/workerd. Generate Env with Wrangler; use
current platform types and schema. A runtime that rejects a future compatibility
date requires an explicit supported date and recorded reason, not a silent test-only
override. Root frontend CI includes monitor lint, strict types and tests.

## Good, base and bad cases

Good: three real failure samples cause one fixed-recipient event and the user later
confirms its receipt. Base: controlled clock/HTTP/email boundaries with real D1
verify deduplication without claiming delivery. Bad: reset an unknown attempt to
pending and silently send the same alert again. Correct: preserve the indeterminate
event and investigate the original provider/user receipt before a separately
authorized retry. A known healthy readiness sample does not establish business or
backup health.

## Trusted operational inputs

Enable external heartbeats only after applying the additive dedicated-D1 schema.
The public HTTP handler remains 404. Queue freshness is 120 seconds; complete
backup freshness is 300 seconds with a required artifact SHA. Validate both source
and delivery timestamps, rejecting missing, negative, non-integer or future data.
Use the source snapshot time, never a newly posted heartbeat, for backup age.
The trusted collector owns actual restore/object verification; a digest alone is
not proof that an untrusted caller backed up anything.

A watchdog may disable readiness only when watching a different monitor key.
Observation age expires after 180 seconds. The main monitor can reciprocally watch
the watchdog. Both share a Cloudflare failure domain; do not claim otherwise.
Keep original failed observations and suppressed events when enabling delivery.
Test fresh and stale operational records through the shipped workerd handler and
real D1, plus the notification state machine with controlled clock/mail boundaries.

## Proven Examples

- `infra/observability/monitor.ts`
- `infra/observability/monitor.test.mjs`
- `infra/observability/schema.sql`
- `docs/ops/cloudflare-alert-activation.md`
