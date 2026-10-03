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

Apply the additive `notification_diagnostics` table before deploying delivery
diagnostics. Failed attempts retain `unknown`; a separate record stores only a
documented Email Service error code, `timeout`, `invalid_receipt`, or
`provider_error`. Commit final event status and diagnostic in the same D1 batch.
Never persist raw exceptions, arbitrary provider codes or recipient details, and
never backfill a cause for old unknown events without evidence. A provider analytics
query returning no events does not prove that an attempted message was delivered
or never sent. Preserve failed deployment receipts; compare returned D1 rows rather
than changing query metadata. On Windows, explicitly pass the existing system proxy
to the Wrangler child if Node does not inherit it; preserve TLS verification.

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

## Optional R2 upload receipts

`probeRemoteBackup` reads the dedicated `BACKUP_BUCKET` only when
`BACKUP_REMOTE_PREFIX=operations-recovery/v1/`; the committed template disables
this input and uses a placeholder bucket. The code is a reader; a normal R2
binding is not a platform read-only authorization. No service/admin credential is
exported to a backup host by this design.

Bound the probe to 10 seconds, five list pages / 4,096 keys, one newest completion
marker of at most 4 KiB and one ciphertext head. Cancel a stalled body reader;
after a timed-out list/get returns, do not issue later requests. Use the single
publisher's UTC capture-start keys to select the newest capture, then validate
the source timestamp inside its marker. New upload time cannot refresh old data.
Keep the original 300-second source age and 15-second clock-skew bounds.

Only paired snapshot ciphertext/completion keys are allowed. Missing, incomplete,
changed, over-budget or malformed state is unhealthy. The capacity threshold
reserves 64 MiB plus 4 KiB for the next maximum-size snapshot; this is an early
alert, distinct from the server's exact additional-byte admission check.

A marker comes from the trusted publisher that performed full remote readback.
Checking marker shape, ciphertext size/ETag and freshness does not independently
authenticate/decrypt the ciphertext or prove actual restoration. Do not substitute
it for the trusted restore-heartbeat/certificate. Keep the two claims separate.
Both old heartbeat and new bucket inputs remain required if both are configured.

Exercise the actual shipped Worker with local workerd/D1/R2. Synthetic bucket
objects and controlled email test stale-source and failure/recovery transitions;
they are not production backup uploads or recipient delivery evidence. Preserve
the existing three/two streak, duplicate-tick and unknown-mail-attempt behavior.

## Server queue observation

`probeReadiness(..., { requireQueueObservation: true })` requires exactly two
fields in `queue`: boolean `healthy` and nullable safe positive integer `source_at`
in Unix milliseconds. Success requires source age <=45 seconds and future skew
<=15 seconds. Missing -> `queue_missing`; malformed/future -> `queue_invalid`;
expired -> `queue_stale`; false -> `queue_unhealthy`. Keep raw responses private
and reuse the same HTTPS limits and D1 three/two incident/delivery contract.

`runScheduled` opts in for `REQUIRE_QUEUE_OBSERVATION=true`; reject other
non-boolean strings and simultaneous `CHECK_READINESS=false`. The template defaults
false. Old heartbeat, backup and watchdog requirements remain active when
configured; no healthy new source may hide another failed input. Migration keeps
the existing key/history, including unknown email attempts.

Producer/API contracts and rollout limits are in `../../backend/resource-metrics.md`.
Queue failure does not change API dependency readiness. The current Service route
covers one Worker. Real workerd/D1 tests cover fresh/stale queue data with an empty
heartbeat table; state-machine tests cover failure/recovery/deduplication and
preservation of an old unknown notification. Tests are not runtime activation or
computer-offline evidence.

For deployment, validate the actual single Worker and advancing API/public source
timestamps before requiring the new input. First require both sources, observe a
natural cron, then clear only `QUEUE_HEARTBEAT_KEY`. A subsequent backup-capacity
reason proves the earlier queue check passed, but keeps overall health failed.
Preserve D1 monitor keys and existing unknown events through both deployments.
Removing desktop inputs does not itself prove a physical computer-off exercise.

When introducing the two network policies to an existing strict release inventory,
bootstrap only the absent policies and compare-and-swap the Namespace prerequisite
fingerprint before binding the normal release plan. Both candidate and rollback
inventories must contain the same complete set. Kubernetes create may emit multiple
JSON objects, and optional labels may be absent; reconcile UIDs/specs after an
uncertain client result instead of repeating successful creates.

## Proven Examples

- `infra/observability/monitor.ts`
- `infra/observability/monitor.test.mjs`
- `infra/observability/schema.sql`
- `docs/ops/cloudflare-alert-activation.md`
