# Manual completion candidate — 2026-10-10

Rows without a model draft can now be completed by a person: choose authorized original
source passages, write the response and prerequisites, save a human draft, review it,
and return the original questionnaire workbook or audit CSV. This closes the local
delivery gap where a terminal generation failure previously prevented final export.

The author, timestamp and rationale are durable and visible. Model attempts/failures
remain unchanged; no successful model attempt is fabricated. Manual work makes no
model/embedding calls or usage reservation. Active generation rejects takeover;
competing writes use revision/idempotency controls. Existing drafts are immutable and
remain editable through review. Manual evidence must be a literal excerpt of a current
authorized packet source; server checks scope/version/generation and derives locations.

## Verification

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
Before deployment, extend the guarded0035→0036 expansion and reader-capability checks,
verify rollback restrictions, publish exact signed artifacts and execute a fresh guarded
window. No staging schema, application, source packet or model-call budget changed in
this implementation phase; staging remains rc47/0035.

This is human delivery capability, not proof of improved model reliability or semantic
accuracy. The frozen public six-row replay remains4 drafts/2 failures/9 calls with its
original partial output. No real-user time savings, willingness to pay, full competitor
parity or commercial acceptance is claimed. Quality and the parent goal remain open.
