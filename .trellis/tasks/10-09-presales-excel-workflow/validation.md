# Local verification — 2026-10-09

The initial Excel implementation phase was locally verified without staging deployment, shared-schema migration, remote push, or live model/embedding calls. Later authorized release and public-replay results are recorded below; this opening table is historical local evidence.

| Check | Result |
| --- | --- |
| Python full non-integration suite | 2,891 passed, 747 deselected, 23 subtests passed |
| Final workbook unit/API suite | 11 passed, including the subsequently added hostile-file cases |
| Existing PostgreSQL presales regressions | 38 passed |
| New workbook PostgreSQL integration suite | 3 passed after correcting the migration test harness naming convention |
| Web full suite | 449 passed in 56 files |
| Final workspace/import suite | 28 passed, including the subsequently added 13-row/batch-boundary regression |
| Ruff check and format | Passed; 722 formatted files checked |
| Mypy | Passed; 271 source files |
| Web lint, typecheck, build | Passed; existing main-chunk size warning remains |
| Real API/PostgreSQL browser workflow | 2 passed: 1440px desktop and 390px mobile |
| Task context manifests | Both validated, 2 real entries each |

The full suites preceded the final additional boundary tests; focused affected suites were rerun afterward. These counts are not claims of a second full-suite run.

Browser acceptance covers upload, mapping confirmation, generation through a controlled provider, review edits, reload and reviewed XLSX download/reopen. It checks unrelated formulas and worksheets, responsive layout and absence of workbook content in browser persistence. Native desktop Excel rendering has not been accepted; original fixed row heights and formula caches are retained.

Recovery and evidence: `D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/manifest.json`, phase `excel_workflow_20261009`; final browser artifacts are under `excel-browser-final-validation/`. Earlier failed traces are retained. Only owned temporary PostgreSQL schemas were removed, with receipts in `excel-resource-cleanup.json` and each subsequent browser run's `cleanup.json`.

The feature commit includes only the Excel implementation, its tests, presales contracts, the usage guide and this child task. Existing commercial/product plan edits and the untracked commercial parent task remain outside that commit. Historical workspace changes are retained. The parent commercial acceptance remains open.

The next phase at that time was schema0035 publication/rollback and a sourced public file replay; that continuation is now complete below. This verification does not establish competitor parity, commercial acceptance, user time savings or willingness to pay.

## Approved continuation: release tooling

The user approved the next stage. The fixed expansion now accepts 0034-to-0035,
and image-only switching on 0035 requires explicit reader capabilities. Legacy
rollback is refused before and after closing admission when workbook history exists.

Validation after these changes: full Python non-integration suite **2,912 passed,
758 deselected, 23 subtests passed** (240.35 seconds); real new and existing schema
expansion PostgreSQL suites **23 passed**; repository Ruff check/format **725 files**
and Mypy **271 source files** passed. Frontend code is unchanged in this continuation.
The first PostgreSQL run exposed a mismatch in the expected PostgreSQL rendering of
the BETWEEN constraint; the exact expected grouping was corrected without weakening
shape validation. The original 0034 error-code context was preserved after regression.

The pre-release read-only staging observation was rc.45/0034, five workloads ready, idle
business, unchanged credential fingerprints and approximately 14.41 GiB free. This
is a preflight, not a live schema upgrade or release. Evidence is in the central task
group: `excel-release-preflight.json` and `excel-release-pytest-nonintegration.log`.
Candidate publication and public file replay were pending at application commit 9ed3857; the subsequent execution follows.

## Executed release and bounded public file replay

- Exact application commit `9ed3857b361573b27abc9f6c245afb1dfdd9feaa`, tag `v0.1.45-rc.46`: Quality 37910201893, Container 37910201958 and signed build 37911371360 succeeded. Five artifacts / 56 evidence files verified. Local test counts above are not remote CI counts.
- Staging0034→0035 expansion, rc46 release, actual rc45 rollback and rc46 reapply passed independent technical verification, taking 72.347 / 90.303 / 83.114 / 85.121 seconds. Complete resources, schema, credentials, history, packaged code, image identity and public assets were checked. No historical cache cleanup was needed.
- Actual rollback preceded the first workbook import. Workbook history now exists: reject legacy readers and preserve history; use compatible images or forward fixes.
- Six frozen SWU requirements derived from a historical public procurement, plus three complete sources (67,879 bytes), ran through actual HTTP upload, ingestion, retrieval, preview/confirmation, import/replay, reload, background generation, assisted review and XLSX/CSV export. This is not the buyer's original workbook or the whole tender.
- **4 drafts / 2 failures / 0 unsubmitted; 9 model calls; 0 manual generation retries.** Both terminal failures followed invalid primary output and fallback timeout. One other invalid primary output recovered through the existing fallback. All four drafts are insufficient-evidence; SWU01 incorrectly used unmet and SWU03 saved irrelevant citations. SWU05 receives a clarity improvement without a new severe-error claim.
- Four Codex-assisted reviews preserved every original draft and attempt; failed rows were untouched. Fully reviewed export returned409. Partial draft workbook contains four reviewed rows plus two failure markers; every unrelated ZIP member, original cell/style and dimension remains unchanged. Native Excel visual acceptance and independent business approval remain absent.
- Independent post-review DB observation: four consumes/two releases exactly once, no active jobs or pending reservations; original workbook hash retained. Review added no model calls or financial/document consumption. Normal ingestion/retrieval made27 embedding calls. Model usage is57,853 known tokens across7 calls,2 calls unknown; financial reconciliation not performed. UTC daily accounting36 application +10 earlier direct =46/200.
- Post-review five-workload identity/readiness and public readiness200 passed. Both short-lived replay/review JWTs were revoked200.
- Owned cleanup: remote233 files/315 directories, local43 transport files and its directory, plus two interrupted-download remnants. Historical caches, records, backups and unrelated worktree differences retained.

See [public replay](../../../docs/ops/public-excel-replay-20261009.md), [release evidence](../../../docs/ops/rc46-release-rollback-validation.json) and [replay evidence](../../../docs/ops/rc46-public-excel-replay-validation.json). Recovery remains in the existing central task group, phase `excel_release_20261009`.

Spec-sync review: schema0035 and workbook-reader contracts were already captured by the implementation/release commits. This closeout changes documentation only; unresolved semantic findings belong to the follow-up report, not a claim of changed executable behavior. Validation for this documentation batch is JSON/context/link integrity and scoped diff checks; previously successful implementation suites are not rerun without code changes.

The bounded Excel milestone is complete with an honestly partial output. Parent commercial acceptance remains open: rc45 seven-success/three-failure/two-unsubmitted history, unrun original160 capacity and independent business/release approval gaps are unchanged. No competitor parity or measured user benefit is claimed.

## Focused remediation candidate

Chinese empty-keyword recall now uses bounded literal character windows within the existing authorization query. Fixed diagnostic categories persist without source/model bodies; prompt v15 clarifies evidence scope and relevant citations. This candidate has not been deployed.

Full non-integration: **2,920 passed / 761 deselected / 23 subtests passed**, 253.92 seconds, using `-B -X utf8`. Three new real PostgreSQL cases passed within an affected run of 192 passed / 1 failed; the sole literal-exception compatibility failure was fixed, then all 26 directly affected tests and the full non-integration suite passed. Ruff format checked 727 files, Ruff check and Mypy 271 sources passed. An earlier non-integration run without UTF-8 failed one evaluation-tool read; no unrelated test was changed.

One bounded maximum-three-call diagnostic lost its SSH control receipt: no reliable request count or output, no retry, three calls conservatively reserved. Its exact orphan was terminated. Fresh read-only verification at 13:47:54 UTC confirmed unchanged deployment/configuration fingerprints, DB0035, six attempts/nine calls, original workbook hash, zero active jobs/pending reservations and public readiness 200. Application day count36 plus previous direct10 plus reserved3 = conservative49/200, not a confirmed call count. This does not establish model reliability, scope reasoning or citation quality.

The candidate report is `docs/ops/public-replay-remediation-20261009.md`; exact source publication, CI, hashes, recovery and unchanged historical worktree state are recorded in the existing central group's `public_replay_remediation_20261009` phase. Keep focused quality work open while preserving the completed Excel milestone.

## rc47 staging verification

Candidate `bdf007ad442d81c48b9f2fa206a4ff38ee761326` / rc47 is deployed on schema0035. Source Quality37940137954, Container37940137868 and signed build37941563636 succeeded;5 artifacts/56 evidence files verified. Actual release/rc46 rollback/reapply took83.853/86.694/85.888 seconds;22 checks per window passed, including existing workbook bytes and mapping. The deployed SWU03 keyword query now returns the two required clauses first; day dispatches remained36, with zero model/embedding calls and no business mutations. This is live retrieval evidence, not model-semantic acceptance. See `docs/ops/rc47-release-validation.json`. Removed only185 remote temporary files/252 directories and43 local transport files plus the earlier owned fragment; retained all images, business history and previous failures.

## Manual completion candidate — 2026-10-10

Pending/failed rows now support human responses with authorized literal evidence,
explicit immutable authorship, separate review and original-file delivery. Migration0036
preserves strict draft JSON in place and refuses downgrade with human history. Old
application readers cannot retain correct attribution; deployment/rollback guards were
still required at this checkpoint and were subsequently verified below. Original public replay is untouched.

Python non-integration2920 passed/23 subtests; Web451 tests/57 files and monitor103 tests
passed. Real PostgreSQL:44 passed in the broader manual/workbook/workflow run;18 older
review cases initially failed on a stale synthetic private model protocol, then all18
passed after updating only that fixture. Final seven manual cases passed including
expired entitlement, no embeddings, audit redaction, raw active states, preservation of
failed accounting, races and migration refusal. Ruff730 files and Mypy273 sources passed.

Four actual browser cases passed at1440/390px with unchanged workbook content and no
new calls for manual cases; one lost committed PUT response recovered via GET without
repeating the write. Initial label-locator failures remain in evidence; explicit
aria-label fixed the shared select. Web lint/typecheck/build passed with the existing
chunk-size warning. Browser-owned schemas were removed with cleanup receipts; integration
fixtures assert their owned schema removal. Historical2993 workspace entries retained.

See `docs/ops/presales-manual-completion-20261010.md` and recovery phase
`manual_takeover_20261009`. This checkpoint records local product completion; the later
live deployment below does not establish provider reliability, semantics or real-user value.

## Guarded manual release tooling — 2026-10-10

Fixed0035-to-0036 expansion now validates inherited/workbook/manual schema shapes and
recovers without replay. Image switching requires separate exact workbook/manual reader
capabilities and refuses incompatible recovery before writes and after all apps stop.
The schema expansion recovery also refuses newly appearing human history.

Validation:2,963 non-integration cases and23 subtests passed (249.17s);286 affected release
tests passed;12 new real PostgreSQL cases and23 earlier expansion regressions passed.
The final extra guard rejecting reader fields on earlier expansion modes then passed
all13 manual expansion tests. Repository Ruff check/format passed733 files; Mypy passed
273 application sources plus the two changed deployment scripts using their Linux target.
The first direct script-type check used Windows and flagged an existing Linux clock
branch as unreachable; the same check also caught one new optional-reader narrowing,
which was fixed. No application code or historical fixtures were changed in this phase.

Real database coverage includes commit/rollback receipt loss, atomic DDL interruption,
missing/defaulted/unvalidated/wrong checks and preserved reviewed human text/authorship,
original XLSX and audit CSV through both compatible recovery and refused legacy recovery.
Owned PostgreSQL schemas were removed by their fixture finally blocks. No provider calls
or staging mutations were made during the local tooling checks. Signed publication and
supervised live acceptance subsequently passed below. Evidence prefix:manual-release-,
central manifest phase:manual_release_20261010.

## Executed rc48 release and human delivery

- Exact signed application6516bd0342d809f1de3a2340b2207bdc94869c75 /v0.1.45-rc.48:
  Quality37964206823, Container37964206894 and signed build37964844999 passed;
  five artifacts /56 evidence files verified.
- Session-pooler backend stability, transaction-surviving advisory lock and contention
  behavior passed before expansion. Full-cache import preserved every original reference;
  no historical image cleanup was needed.
- Actual0035-to-0036 expansion, release/rc47 rollback/reapply completed in69.210 /86.419
  /86.398 /80.947 seconds. Independent schema verification and all22 image-window checks
  passed, including signed packaged sources/assets, credentials, original workbook/frozen
  packet, history, idle accounting, capacity and five ready workloads.
- One new labeled one-row workbook passed public HTTP preview/import, literal evidence,
  human save, reviewed-export409 before review, separate review, GET reload and reviewed
  XLSX/CSV. Human authorship/original text remained explicit; the formula and separate
  worksheet were intact. All global Job/attempt/provider-call/reservation/dispatch counts
  remained unchanged. No model/embedding calls, route changes or budget increases.
- The initial harness failed before import when openpyxl tried to create temporary files
  inside the read-only application container; its token was revoked. Retained the failure,
  generated the workbook locally, and checked that no prior owned packet or human record
  existed before continuing. No uncertain write was retried. Final token revocation200 passed.
- Human history now exists: the actual ReleaseCluster.restore path was refused before
  any Kubernetes write through a read-only boundary. rc47/rc46 are no longer eligible
  recovery targets; retain authorship and use compatible readers or a forward fix.
- Removed only233 remote temporary files /315 directories and43 local transport files
  /1 directory. Kept images, signed evidence, original failures, business data and all2,993
  unrelated worktree entries. Post-cleanup five-service identity and public readiness200 passed.

See `docs/ops/rc48-manual-release-validation.json` for receipt hashes. Central recovery
phase:manual_release_20261010; source baseline:c21144d2b1283ded2508bd81c5c29a53675f3309;
doc checkpoint:6516bd0342d809f1de3a2340b2207bdc94869c75. This closes the authorized
human-delivery release milestone. The six-row model replay remains4 drafts/2 failures/9
calls, the unknown diagnostic is not retried, and original160 capacity is still unrun.
No real-customer or independent business approval, measured time saving, paid demand or
competitor-parity claim. Doc closeout uses JSON/evidence/context/diff integrity checks;
successful code suites are not repeated for unchanged executable sources.
