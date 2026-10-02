# External service and operations monitor

This independent Cloudflare Worker checks the deployed application's `/health/ready`
every minute. It adds no process to the application server. The existing private
mailbox remains the inbound mail service; this Worker has its own D1 state and a
restricted Email Service binding.

- Accept only HTTP 200, JSON, all dependencies up and an aware timestamp no older
  than 120 seconds (maximum future skew: 15 seconds). No redirects or retries;
  10-second total probe deadline and 64 KiB body bound.
- Three consecutive failed minutes open an incident; two consecutive healthy
  minutes close it. Initial health does not send a recovery message. Missing
  minutes reset the streak. Duplicate, out-of-order and expired samples do not count.
- D1 atomically commits the sample, incident transition and unique notification.
  Pending events survive an interruption before dispatch; each tick drains at most
  two pending events. The sender claims each event before using the email binding.
- `accepted` means the binding returned a message identifier. It does **not** mean
  the user received the email. `unknown` and an abandoned `attempting` are
  indeterminate and never automatically retried. Preserve these records for review.
- Failed attempts also write a bounded code to `notification_diagnostics`: a
  documented provider error, `timeout`, `invalid_receipt`, or `provider_error`.
  Raw exceptions and arbitrary error codes are discarded. Apply this additive
  table from `schema.sql` before updating an existing monitor. Original unknown
  events remain unchanged and are not replayed by this upgrade.
- No HTTP administration or arbitrary email API. `workers_dev` and preview URLs are
  disabled; the HTTP handler returns 404. No service credentials are needed.

## Configuration and validation

The committed configuration is an **observe-only template** with placeholder D1
and email addresses. Prepare a private configuration outside the repository, with
an absolute `main` path, the exact account/database identifiers, and matching
single-address sender/recipient allowlists. Never deploy the placeholder file.

```powershell
pnpm install --frozen-lockfile
pnpm --filter ops-monitor types
pnpm --filter ops-monitor lint
pnpm --filter ops-monitor typecheck
pnpm --filter ops-monitor test
pnpm --filter ops-monitor exec wrangler deploy --dry-run --config <private-config> --outdir <task-evidence-directory>
```

Wrangler 4.142.0 and its matching Miniflare 5 alpha/workerd dependency are pinned in
the lockfile. The tests use the published V4-options converter, real local D1 and
the shipped handler in workerd; only the target HTTP service and email delivery
boundaries are controlled. Compatibility date 2026-09-26 is the pinned runtime's
supported date; the local 2026-09-28 date was rejected as future and is not silently
substituted during testing. Regenerate Env with Wrangler after binding changes.

## Activation and delivery drill

Follow [the activation plan](../../docs/ops/cloudflare-alert-activation.md).
Start with `MODE=observe`, `MONITOR_KEY=readiness-v1`, `DRILL_START_MS=0`, and apply
`schema.sql` only to the newly created dedicated database. Verify at least three
real scheduled observations before notification activation.

A separate, temporary drill Worker uses the same reviewed source, a distinct
`drill-<id>` key and an explicit future minute-aligned `DRILL_START_MS`. Its five
minutes supply three controlled failures followed by two successes, make **zero**
requests to the production target, and generate at most one labelled failure and
one recovery event. Late or missing cron ticks can make the drill fail; do not
change timestamps or reset events to claim it passed. It cannot change production
monitor state. Archive its evidence before removing its cron/Worker.

To activate real delivery, verify the fixed destination and sender domain, then
use `MODE=notify` with a **new readiness key**. This starts a fresh streak; events
suppressed in observe mode are never relabelled as delivered. Confirm both test
messages with the recipient separately from the binding acknowledgements.

For rollback, set `MODE=observe` or remove only this Worker's cron; retain D1 events
and the source/configuration/version receipt. Do not delete the existing mailbox,
its bindings, Routing rules, MX records or any application resources.

## Queue, backup and scheduler freshness

Optional `QUEUE_HEARTBEAT_KEY` and `BACKUP_HEARTBEAT_KEY` select records in
`external_heartbeats`. A trusted private collector writes them through the D1
management API; the Worker exposes no write endpoint. Apply the additive schema
to the dedicated monitor database before enabling these keys.

The queue record expires after 120 seconds. The backup record expires after 300
seconds and requires a verified artifact SHA-256. Both `source_at` and `observed_at`
must be valid millisecond timestamps; future skew is limited to 15 seconds.
Reposting an old backup does not refresh its source time. A missing, failed,
invalid or stale record makes an otherwise healthy readiness observation fail.
Collectors must derive backup time from the consistent database snapshot, and
publish success only after actual database restore and persistent-object checks.

`WATCH_MONITOR_KEY` checks another monitor's last observation, expiring after 180
seconds. A separate Worker uses `CHECK_READINESS=false` to watch the main schedule;
the main Worker watches it in return. Disabling readiness without another monitor,
or watching the same key, is rejected. Synthetic drills bypass these real sources.
All failures use the same three-failure/two-recovery state machine and delivery
deduplication. A watchdog checks scheduling, not whether the other monitor reports
healthy service.

## Optional server-upload monitoring

The template keeps `BACKUP_REMOTE_PREFIX` empty, so existing deployments retain
their heartbeat behavior. Before enabling the new input, replace the placeholder
`BACKUP_BUCKET` binding with the specifically approved backup bucket and set
`BACKUP_REMOTE_PREFIX=operations-recovery/v1/`. Match `BACKUP_REMOTE_MAX_BYTES` to
the server's cap (at most 3 GiB). The monitor code only lists/reads/heads objects;
the bucket binding itself is not a platform-enforced read-only credential.

The probe lists at most five pages / 4,096 keys, then reads the latest capture's
completion marker (at most 4 KiB) and heads its ciphertext. Its total deadline is
10 seconds, including the streamed marker body; a timed-out body reader is
cancelled. Only the server's timestamped snapshot keys are accepted. Unknown keys,
unpaired ciphertext/markers, changed objects, malformed receipts and source times
older than 300 seconds fail the observation. A fresh upload of an old snapshot
remains stale. Capacity alerts reserve space for one next maximum-size snapshot
plus its marker, so they can fire before publication reaches the hard cap.

This is **upload receipt and freshness monitoring**. The trusted publisher has
already read back the ciphertext; this probe checks its marker, current object
size/ETag and source time without decrypting or rehashing the complete ciphertext.
It does not convert an `actual_restore_verified=false` marker into a restore pass.
Keep the separate trusted restore evidence and protection catalog. Enabling this
input does not automatically disable either existing heartbeat or stop Windows
collectors. All configured inputs must pass; changing that selection belongs in
the concrete production deployment plan.

The candidate is exercised with real local Miniflare/workerd, D1 and R2, including
stale-source rejection and one failure/recovery notification through the existing
deduplication contract. Local tests use synthetic objects and controlled mail;
they do not send mail, upload production backups, or prove production R2 access.
Production configuration, activation and computer-offline evidence remain pending.

## Operational limits

The two Workers share Cloudflare and D1; this is not coverage for an entire
Cloudflare outage. The deployed private collector currently depends on signed-in
Windows, SSH and Docker Desktop. Minute triggers skip overlapping work. Backups
are retained in the existing recovery group, with a 3 GiB stage dump budget and no
automatic deletion. Offline collectors or exhausted storage produce failed/stale
heartbeats, not a healthy status. This bounded configuration is not 24/7 coverage
or proof of whole-host RPO/RTO. See the [current acceptance record](../../docs/ops/final-project-acceptance.md).

Events are retained without automatic deletion. No automatic reset/replay is
permitted for an indeterminate email attempt. Mail quota errors stay visible;
changing providers or retrying after such an event needs a new bounded test.
