# Manual completion deployed — 2026-10-10

Rows without a model draft can now be completed by a person: choose authorized original
source passages, write the response and prerequisites, save a human draft, review it,
and return the original questionnaire workbook or audit CSV. This closes the
delivery gap where a terminal generation failure previously prevented final export.

The author, timestamp and rationale are durable and visible. Model attempts/failures
remain unchanged; no successful model attempt is fabricated. Manual work makes no
model/embedding calls or usage reservation. Active generation rejects takeover;
competing writes use revision/idempotency controls. Existing drafts are immutable and
remain editable through review. Manual evidence must be a literal excerpt of a current
authorized packet source; server checks scope/version/generation and derives locations.

## Local product verification

- Full Python non-integration: 2,920 passed,23 subtests. Subsequent changes only add
  CSV original-human prerequisite output, a pagination cap fix and test assertions;
  their affected checks are recorded below, not claimed as a second full-suite run.
- Web:451 tests in57 files; operations monitor:103 tests. Lint/typecheck/build passed.
  Existing >500KB main-chunk build warning remains.
- Real PostgreSQL/API:44 cases passed across manual completion, original workflow and
  workbook coverage.18 older review tests initially failed during fixture setup because
  their controlled response used a retired private protocol; updating only that
  synthetic fixture to the current literal-definition contract made all18 pass.
- Browser:4 cases passed at1440px and390px, including the existing model workflow and
  human-only delivery. A deliberately lost successful manual-save acknowledgement
  recovered through GET with no second PUT. Human-only cases added zero provider calls.
  XLSX reopened with answers, unrelated formula/worksheet preserved. Screenshots reviewed.
- The first browser run exposed an unstable wrapping-select label. An explicit
  assessment aria-label fixed it; original failed traces remain retained.
- Final seven manual PostgreSQL/API cases passed with expired entitlement, audit
  redaction and an embedding boundary that fails if called. Ruff730 files and Mypy273
  sources passed. Browser schema-removal receipts and fixture cleanup checks passed.

Evidence is in the existing recovery group under `fallback-model-evidence`, prefix
`manual-takeover-*`; the manifest phase is `manual_takeover_20261009` (the continuation
crossed local midnight). Source recovery uses the exact fd0cb48 Git baseline for clean
tracked files; new paths are recorded as originally absent. Existing2,993 unrelated
working-tree entries are outside this change.

## Persistence and release boundary

Migration0036 adds a nullable authorship side column; it leaves historical strict
SavedDraft JSON unchanged. Schema downgrade refuses any manual history. Old rc47/rc46
applications can parse draft bytes but would misattribute human text, so neither is a
valid rollback target after human records exist. API/Worker/Web must move together.
The release continuation implements guarded0035→0036 expansion and independent
workbook/manual reader checks. Both release race boundaries reject incompatible readers;
schema recovery also refuses reopening the original applications if manual history appears.
Twelve new real PostgreSQL cases and23 earlier expansion regressions passed, including
atomic rollback, lost receipts, exact shape checks and human-record/workbook preservation.
The signed candidate is now deployed as rc48/0036, with the live results below.
No original source packet, model route, retry allowance or call budget changed.

## Staging release and human delivery

Application `6516bd0342d809f1de3a2340b2207bdc94869c75`, tag `v0.1.45-rc.48`:
source Quality37964206823 and Container37964206894 passed, as did signed build37964844999.
Five artifacts /56 evidence files were verified. Release-tooling checks passed2,963
non-integration cases /23 subtests,286 affected deployment cases,12 new PostgreSQL
cases and23 older expansion regressions. Final additional old-mode rejection passed
all13 manual expansion tests; Ruff733 files and Mypy273 application sources plus the
two deployment scripts passed. These are recorded runs, not new repeated suites.

Supervised0035-to-0036 expansion, rc48 release, actual rc47 rollback and rc48 reapply
passed independent verification in69.210 /86.419 /86.398 /80.947 seconds. Rollback
preceded the first human record and retained schema0036. All22 checks passed in each
image window, including packaged source, signed assets, original workbook, credentials,
idle accounting and five ready workloads.

One new one-row workbook completed the public HTTP path: preview/import, authorized
literal evidence, human draft, blocked reviewed export before review (409), separate
review, reload and reviewed XLSX/CSV. Author/time and original human text survived;
the formula and separate worksheet were preserved. Jobs, attempts, provider calls,
reservations and dispatch counts did not increase. The short-lived token was revoked.
This was labeled Codex-assisted technical acceptance, not a customer or independent
business approval. The terminal-failed-row path remains covered by local PostgreSQL/API
tests; the new live row started pending.

The initial harness failed before import because openpyxl attempted temporary-file
creation inside the read-only application container. Its failure and token revocation
were retained. A local input workbook replaced that harness operation, with a read-only
check proving no prior owned packet or human record before proceeding. No application
configuration was changed and no uncertain write was retried.

Human history now exists. A read-only Kubernetes boundary exercised the real restore
guard against that history and verified rejection of rc47 before any write. Recovery
requires compatible human readers (rc48 or a forward fix); never remove authorship or
downgrade schema to make an old version eligible.

Cleanup removed only233 remote temporary files /315 directories and43 local transport
files /1 directory. Images, historical files, business records and central evidence were
retained. Post-cleanup five-workload identity and public homepage/readiness200 passed.
Detailed checks and evidence hashes: [release record](rc48-manual-release-validation.json).
Recovery remains in the same central group, phase `manual_release_20261010`, source
baseline `c21144d2b1283ded2508bd81c5c29a53675f3309`; documentation checkpoint is the
deployed application commit above. All2,993 unrelated worktree entries remain unchanged.

This is human delivery capability, not proof of improved model reliability or semantic
accuracy. The frozen public six-row replay remains4 drafts/2 failures/9 calls with its
original partial output. No real-user time savings, willingness to pay, full competitor
parity or commercial acceptance is claimed. Quality and the parent goal remain open.
