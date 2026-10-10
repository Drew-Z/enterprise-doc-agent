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

## Review citation correction candidate — 2026-10-10

An existing draft can now be reviewed with a new ordered selection of exact authorized
source passages. Each review owns its evidence snapshot; the original draft, earlier
reviews and all attempts remain unchanged. Current/history UI and CSV follow those
bindings, including an explicit empty selection. Correcting citations requires a note;
removing citations unselects affected prerequisite links and forces deliberate rebinding.
Shared literal search does not reset unsaved response text. A lost review PUT result is
recovered only by GET. No new model routes, calls, budgets or embedding requirements.

- Backend contract red:3 rejected-citations tests, then3 green. Real API red additionally
  reproduced missing server metadata; after resolver/storage work,10 cases passed.
- Final Python checks: Ruff,format736,mypy274;2,967 nonintegration tests and23 subtests.
- Affected PostgreSQL suites: review-citations,review-changes,manual-response,workflow,
  workbook:77 unique cases passed. First combined run had39 pass/1 test assertion failure
  (source revocation actually returns404); corrected expectation then passed in the
  38-case workflow/workbook/revocation run. No shared schema migration.
- Web: lint,typecheck,457 tests,production build passed. Existing>500kB bundle advisory remains.
- Browser: original model workbook workflow passed at1440/390; human correction initially
  timed out using an exact label locator despite the rendered textbox. Switching the test
  to its accessible textbox role passed both widths (5.0s/4.2s). Preserved failure traces.
  Verified lost manual/review responses, one PUT per intent, read/reload, corrected CSV,
  original quote, formula and other-sheet preservation, no horizontal overflow and no
  source/file bodies in browser storage. Human flows added zero controlled gateway calls.
- Browser schemas removed with matching ownership/cleanup receipts. Screenshots visually
  checked at both widths. All2,993 unrelated Git entries retain their original status hash.

Report: `docs/ops/presales-review-citations-20261010.md`. Central recovery phase:
`review_evidence_20261010`, Git baseline `9fb1d4454cf42782f6443634ce4abf84c748476a`.
This is a0037 candidate; staging remains rc48/0036. A coordinated expansion and compatible
reader/rollback guards are still required before deployment. Do not restore rc48 after
explicit review citation history exists. Frozen public failures and call ledger unchanged;
human repair does not establish model accuracy, customer time savings or competitor parity.

## Guarded review citation release tooling — 2026-10-10

Fixed0036-to-0037 expansion now verifies all inherited shapes and the nullable citations
array constraint in the same private locked session. Recovery reconciles complete state
without replay and refuses citation history before writes/before reopening old readers.
Schema0037 image plans independently bind workbook/manual/citation capabilities, including
both close races and explicit[] history. Existing resource and idle restrictions remain.

Final checks:3,029 Python nonintegration tests and23 subtests passed,810 deselected
(289.55s);1,367 deployment cases passed (163.60s); all four real PostgreSQL expansion
suites passed50 cases (131.69s), including15 new citation cases. Real records preserve
original draft, earlier review, workbook, CSV and accounting across refused/compatible
recovery. Ruff check/format739 and Mypy274 application sources plus two Linux-target
deployment scripts passed. The initial Windows script check flagged the existing Linux
clock branch; no ignore or unrelated code change was made. Frontend sources are unchanged.

Report:`docs/ops/presales-review-citations-release-20261010.md`. Recovery phase:
`review_citations_release_20261010`, baseline7cc745ac937dd50c275d0b12d464bb26e55c5f77.
Owned PostgreSQL schemas are removed by fixture cleanup. This checkpoint performs no
shared migration, staging mutation or model/embedding call. Signed publication and live
release remain next; last verified staging is rc48/0036. Preserve frozen outputs and
unknown diagnostics; parent commercial acceptance remains open.

## Executed rc49 release and citation delivery

- Exact application `fe995676649570328892e2b48d80f02ee9526dd8`, signed tag
  `v0.1.45-rc.49`: Quality 37981892144, Container 37981892045 and signed build
  37982424490 passed; five artifacts / 56 evidence files verified.
- Initial full-batch capacity preflight rejected before target writes, short by
  836,391,680 bytes. User approved exactly 32 rc36–37 cache references; independent
  verification found no unselected changes, all 40 protected references retained and
  2,214,735,872 bytes net space released. The unchanged aggregate capacity guard passed.
  All 19 actual-archive alias-preservation checks passed against the fresh baseline.
- Fixed 0036→0037 expansion took 74.649 seconds; release / actual rc48 rollback / rc49
  reapply took 85.770 / 83.024 / 86.277 seconds. All 28 schema checks and 22 checks per
  image window passed, including complete resources, signed packaged sources/assets,
  credentials, original workbook/frozen packet, previous human history and idle accounting.
- One newly labeled one-row workbook passed HTTP import, human draft using an irrelevant
  old quote, legacy review, independent corrected review, GET reload and reviewed XLSX/CSV.
  Original draft/citation/authorship and earlier review remain intact. Current evidence and
  prerequisite index bind the corrected quote; original formula and other sheet remain.
- The first harness confused external label SWU03 with persisted key X7 and failed before
  creation. Read-only diagnosis proved no prior owned packet, no citation history and unchanged
  ledger. Corrected key/question/B7–C7 binding passed. Original failure retained; no uncertain
  write retried. Failed, diagnostic and successful-run tokens were all revoked.
- Six global accounting counters stayed unchanged: jobs 3553, attempts 541, provider calls
  597, usage reservations 541, product reservations 613 and dispatches 597. Zero new model
  or embedding calls. Frozen public and prior human packets remained identical through GET.
- With new citation history present, actual `ReleaseCluster.restore` refused old readers
  before any Kubernetes write through a read-only boundary. rc48/rc47/rc46 are now ineligible
  recovery targets; retain all history and use compatible readers or a forward repair.
- Removed only 233 remote temporary files / 315 directories and 43 local transport files /
  one directory, in addition to the separately approved cache references. Final unchanged
  five-service identity, homepage 200 and readiness 200 passed. Historical data, signed
  artifacts, failures, backup evidence and unrelated worktree entries remain.

See `docs/ops/rc49-citation-release-validation.json` and
`docs/ops/presales-review-citations-release-20261010.md`. Recovery stays in phase
`review_citations_release_20261010`; doc baseline is fe99567. Existing release specifications
cover the final behavior. Closeout checks are JSON/evidence/context/diff integrity only;
unchanged executable suites are not rerun. Original public results remain 4 drafts / 2
failures / 9 calls, the diagnostic remains unknown and original160 is unrun. Human repair
does not establish model accuracy, customer time savings, paid demand or competitor parity.

## Generation failure diagnostics and CQU03 — 2026-10-10

The collector now preserves the existing gateway's allowlisted errorDiagnostic for
new failed reports, omitting it when unavailable. The real HTTP-boundary regression
failed with KeyError before the fix;49 collector/stream/scorer/diagnostic cases passed
afterward. Full checks passed: Ruff739-file format/check, Mypy274 sources,3,029 Python
nonintegration tests and23 subtests (810 deselected,266.05s). Frontend, application
gateway/prompt, routes and database code were unchanged.

One previously unused public CQU03 requirement and separate reference were frozen
before the single current-primary request. It returned a schema-valid draft in40.718s,
insufficient_evidence, unknown state and the exact relevant citation. Mechanical
scoring passed. Codex-assisted semantic review failed completeness: no testing
arrangements, no failed-test remediation in prose, and a necessity rule used as the
unknown proposition instead of actual completion. This does not explain old failures.
The first response and all criteria are retained; no retry or second sample occurred.

Read-only rc49/0037 checks before/after found five ready workloads, identical packaged
gateway sources/policy and unchanged application accounting. One direct request is
separately reserved:36 app +13 prior known/unknown direct +1 new =50/200 conservative
budget, including the earlier three unknown calls. No embeddings, tenant writes,
deployment or frozen-packet edits. Original public replay remains4/2/9.

Report: `docs/ops/generation-diagnostic-cqu03-20261010.md`. Recovery phase
`generation_failure_diagnostic_20261010` binds seven clean files to58c1e5c and records
the new report absent. All2,993 unrelated Git entries retain the original status hash.
Next implementation must address complete requirement coverage and business-event
versus rule meaning, without treating this one diagnostic as a benchmark or acceptance.

## Rejected coverage instruction candidate — 2026-10-10

The v16 candidate changed only model-facing field descriptions and prompt wording.
It passed174 focused protocol/scoring tests,739-file format, Mypy274, and3,029
nonintegration tests/23 subtests (810 deselected,262.29s). Ruff rejected17 fullwidth
punctuation occurrences in new strings; this failed check is retained. Unit/schema
success does not establish semantic performance.

One pre-frozen new CQU01 case completed in27.625s with a valid draft and exact citations.
Classification/anchors passed; separate assistant review rejected it against the frozen
criteria: no specific interface/data-model/integration-plan inquiry, and the proposition
converted the question's verification request into a supplier verification obligation.
The prompt-only candidate therefore is not eligible for release. No second call.

Both runtime source files were snapshotted with hashes in the existing recovery group,
then restored byte-for-byte to d95475e. Runtime remains v15; historical outputs and
decoder behavior are unchanged. Only task/spec/report documentation is delivered.
Read-only staging checks confirmed rc49/0037, same policy, idle workloads and unchanged
application ledgers. One direct call is separately reserved; conservative budget51/200
includes the previous three unknown calls. No embedding, product write or deployment.

See `docs/ops/answer-coverage-candidate-20261010.md`; recovery phase
`answer_coverage_candidate_20261010`. Next design separates requested answer aspects
from source-defined business prerequisites instead of further instruction-only sampling.

## Rejected separate answer protocol — 2026-10-10

Implemented a private CoverageDraft and deterministic question-span projection.
Thirteen focused tests passed, including red/green per-item evidence-or-gap
validation, source selection, quote, language and final-size boundaries. Initial
Ruff import/punctuation findings were corrected; final check/format passed.

One frozen new CQU04 request on the unchanged primary route took9.390s and failed
with draft_schema: missingInformation was a string, not an array. Rejected raw text
also lacks separate, concrete encryption and display-masking evidence requests.
No retry, normalization, historical rerun or scorer reinterpretation. The run-v7
report is candidate-only; runtime v15 and historical v1..v6 decoders stay unchanged.

The exact two new candidate files were centrally snapshotted and hash-verified
before withdrawal. Final change is documentation only, so full runtime/DB/browser
suites are not repeated for a protocol that is not shipped. The 13 local tests do
not establish model quality. Read-only staging postflight confirms rc49/0037,
five ready workloads, zero active jobs and unchanged application accounting/policy.
Conservative UTC-day call budget52/200 includes16 direct known/reserved calls;
three previous unknowns remain reserved. No embeddings, product write or release.

Recovery phase: answer_coverage_protocol_20261010, baseline8a13ff8. See
docs/ops/answer-coverage-protocol-20261010.md. Competitor acceptance remains open.

## Optional strict output request — 2026-10-10

Nine core and two evaluation tests failed before implementation and passed after.
The143-test focused suite includes frozen historical scoring. Eighteen real owned
PostgreSQL cases verify strict synchronous/background persistence, idempotent
replay, a single charge, and mode drift rejected before dispatch with reservations
released. Owned schemas are removed in fixture finally. Whole-project checks:
Ruff format743 files, Ruff check, Mypy275 source files,3,040 nonintegration tests
and23 subtests pass (814 deselected,262.09s). No frontend or public schema changes.

One newly frozen synthetic primary/low/streaming request returned HTTP200 with
schema-valid data in37.563s. Actual business validation failed support_combination:
merged prerequisites, positive and negative with uncertainty none, and no question
for unknown acceptance. Original failed report stays failed; no normalization,
retry, public case replay, deployment or staging enablement. Explicit opt-in remains
false. Read-only pre/postflight confirms unchanged rc49/0037/v15/policy/ledgers,
five ready services and zero active jobs. Conservative budget53/200 includes17
direct known/reserved calls and the three prior unknowns; no embedding calls.

Recovery phase strict_output_contract_20261010 binds Git baseline500185e and records
new files absent. The initial single fullwidth-colon Ruff finding was fixed; evidence,
final publication and owned-temp cleanup receipts are retained centrally. See
docs/ops/presales-strict-output-20261010.md. Parent competitor acceptance remains open.

## Provider-visible evidence combinations — 2026-10-10

The new native Pydantic union exposes four mutually exclusive support alternatives
to strict output v19. The 24-case truth table checks JSON Schema and local decoder
agreement; five valid states include missing with/without an unconfirmed record.
Source-bound projections retain met/unmet/unknown and actual version conflict.
Literal quotes, authorized references, Chinese answers, confirmation questions and
size bounds remain enforced. This does not prove atomic decomposition or entailment.

Four boundary tests failed against the old producer before integration; all 45
combined new/gateway/evaluation tests pass. Eighteen real PostgreSQL cases pass
(40.78s), with owned schemas removed in fixture finally. Full nonintegration:
3,074 passed, 814 deselected and 23 subtests passed (263.39s). Ruff format/check
covers 745 files; Mypy passes 276 source files. No frontend/public schema migration.

Run-v9 uses the new schema/resolver; run-v8 retains the byte-identical v18 contract.
Offline evaluation of the immutable original failed response confirms the new
schema rejects its first prerequisite at anyOf, while the original failed report
and historical score remain unchanged. Default v15 prompt identity is unchanged.
Frozen v18 work cannot be restored as v19; future releases must drain it first.

Recovery phase prerequisite_support_schema_20261010 binds baseline7a468d9 and records
new-file absence, source hashes, offline evidence, publication and cleanup receipts.
The 2,993 unrelated workspace status entries retain their original checksum.
This slice made zero model/embedding calls and no staging changes. Larger-schema
endpoint compatibility, semantic improvement and competitor acceptance remain open.
See docs/ops/presales-support-contract-20261010.md.

## V19 mixed-state observation — 2026-10-10

One new synthetic S1901 fixture and separate frozen criteria exercise four distinct
business events on the existing primary/low/streaming route. The exact d74df4e
gateway/controller, v19 prompt/schema, source hashes and fresh daily budget were
bound before a durable single-call intent. No old sample, gold, route or retry changed.

HTTP200 returned in39.765s, one dispatch and7,146 reported tokens. Both JSON Schema
and ConstrainedBasisDraft accept the field combinations. The real gateway rejects
support_quote: prerequisites[1].negative[0] and prerequisites[2].negative[0] add a
date/order prefix not present as a literal span in that source. No quote repair.
Mechanical scoring retains failed with zero accepted drafts. Separate semantic
review finds missing rehearsal status wrongly represented as negative/none, despite
unknown wording in prose. Four events were separated and the same-scope peer-record
conflict was retained; neither fact repairs the old STRICT01 sample or proves a rate.

Read-only postflight confirms the original policy, module hashes, DB0037, ledgers,
zero active jobs and five ready services. UTC2026-10-09 conservative usage54/200
includes36 app dispatches and18 known/reserved direct calls. Zero new embedding,
staging writes, retries or deployment. The initial freeze hit the existing synthetic
ASCII filename constraint before gold/plan/intent or inference; only filenames were
corrected and the rejected pre-inference artifact remains preserved.

Recovery phase support_schema_live_probe_20261010 binds baseline d74df4e; central
input, criteria, plan, intent, original result, mechanical score, semantic review
and postflight hashes are retained. Documentation-only repository changes need no
new runtime test run; d74df4e's prior test/CI results remain that code's evidence.
See docs/ops/presales-support-contract-probe-20261010.md. Promotion and competitor
acceptance remain unfulfilled.

## Immutable evidence span selection — 2026-10-10

The new strict v20 contract selects only offered span IDs for every definition and
directional/missing quote. Complete original context and lexical substrings remain
available; exact parent citations are materialized server-side and then traverse
the unchanged v19/basis rules. No source relabeling or generated-text normalization.

Red/green checks began with absent offering/projection APIs, one HTTP-boundary
failure against the old gateway and two collector-version failures. Final focused
suite:54 passed, including42 new module cases and24 truth-table combinations.
Tests cover all four quote fields, contextual qualifiers, states and source versions,
foreign/cross-call IDs, old quote objects, wrong semantic direction left unchanged,
input-size refusal before dispatch, changed span catalogs and both scorers.

Eighteen real PostgreSQL cases pass in40.85s: synchronous and restored background
execution persist selected original evidence, replay safely and charge once; mode
drift refuses before dispatch. The first run had two setup failures because a new
chunk reused generation index0. A fresh owned document/generation fixes the fixture;
no product constraint was relaxed. Fixture-owned schemas are removed in finally.

Full nonintegration:3,117 passed,814 deselected and23 subtests passed in268.00s.
Ruff format/check passes747 files; Mypy passes277 source files. Initial escaped
punctuation/line-length findings were fixed without changing the runtime strings.
Default v15 prompt SHA remains318fc29ef2903cef5ad51a59163fad35ff855aca012bbae986e84a0fbb83d2ab.
Strict v20 prompt SHA isb2edcfdc8b9f9296f629ace9bf9255b6e7f9b34e86b787ef392cdc265b517375.

Offline rescoring preserves exact v18/v19 failed reports and score values. Their
schema modules remain byte-identical to8dc0b31. Run-v10 verifies the entire recorded
span input before interpreting success or failure; old reports keep old resolvers.
Accepted v18/v19 policies cannot restore through v20. No new model/embedding calls,
deployment or staging enablement. Actual endpoint behavior, semantic correction
and competitor acceptance remain unproven.

Recovery phase presales_span_selection_20261010 registers16 paths at baseline8dc0b31;
the central manifest records checks, offline evidence, source hashes, publication
and owned-temp cleanup. The2,993 unrelated status entries retain their baseline
checksum. See docs/ops/presales-span-selection-20261010.md.

## V20 mixed-state observation — 2026-10-10

One new S2001 synthetic fixture was frozen with separate criteria against c3ab10d.
The unchanged primary route returned HTTP200 in52.016s, with9,130 reported tokens.
Strict schema, immutable span resolution, the production decoder and mechanical
source/status checks passed. Semantic criteria3/4/8/9 failed: a missing verification
record became negative/none, four definition slots held current-state facts instead
of necessity rules, and two next actions were absent. Five separate events, training
met despite absent certificate, correct authorization conflict and exclusion of the
other order do not outweigh those failures. Keep original results without repair.

The original run-v10 result SHA is
ebab2f645c31cb4c28b0aa9a43d6f9979085d8974fc8ad74879341306405e74a.
Input/reference/controller/source hashes bind the first and only request. Controller
syntax/import and strict schema validation passed. No runtime source changed, so
the prior exact-source CI and3,117-test result remain the product check evidence;
no redundant model or unit-test run was used to manufacture a new outcome.

Read-only postflight verifies rc49/0037/v15, source/policy hashes, five ready services,
zero active jobs and unchanged ledgers. UTC2026-10-10 has0 application dispatches and
one new direct request; conservatively retaining18 previous-day known/unknown
reservations yields19/200, not19 observed current-day calls. No embedding or staging
change. Six documentation paths have Git/new-file recovery at c3ab10d under phase
span_selection_live_probe_20261010. Final documentation/publication and workspace
checks are recorded centrally. No disposable resources were created.

See docs/ops/presales-span-selection-probe-20261010.md. Strict mode promotion, public
task quality, independent review and the competitor-standard goal remain open.

## Standalone rule/assessment/response candidate — 2026-10-10

Private protocol presales.assessment-candidate.v1 separates selected necessity
rules from typed state assessments and exact requirement-part responses. Every
rule must have one assessment; each unmet/unknown item requires its own action.
Per-part gaps remain next to their question and in the global missing-information
list. Reuse the original literal span resolver; labels cannot mask non-Chinese
prose, and final-size overflow rejects without truncation.

The first public parser test failed while the API was absent. A second red test
demonstrated loss of per-question gap placement before the projection fix. Final
checks:41 candidate tests and86 combined boundary cases pass;3,158 nonintegration
tests and23 subtests pass in277.32s (814 deselected). Ruff format/check covers749
files, Mypy passes278 source files. Initial ambiguous punctuation and long lines
were corrected with equivalent Unicode escapes and formatting. No behavior was
suppressed to pass checks.

The candidate has no production gateway/scorer/settings import. Ten runtime source
files, v15/v20 prompt identities and the original v18/v19/v20 result hashes remain
unchanged. No database or execution-policy code changed, so no additional database
integration run is claimed. Controlled wrong-role/missing-as-negative examples
remain semantic errors; the parser does not repair them. A source clause may
legitimately serve both rule and state roles. Actual-model acceptance is unproven.

Candidate prompt SHA f274eeba7c7f7c32b7420c2c2d0f37246f170bde0504f496b0f3bf8677f13b07.
Recovery phase presales_assessment_candidate_20261010 binds eight paths to adb07d6
or recorded new-file absence. Zero provider/embedding calls and staging changes.
Central records retain offline evidence, checks, publication and exact owned-temp
cleanup. No historical outputs or unrelated files are discarded. See
docs/ops/presales-assessment-candidate-20261010.md for promotion boundaries.

## Assessment candidate observation — 2026-10-10

AR01 freezes six prerequisite states for fictional order CH-2610 before a single
primary grok-4.7/low streaming request, with a separate reference and another-order
scope distractor. Controlled adapter success/503 each dispatched once without retry
and restored the original gateway identity. The first local fixture lacked three
metadata fields and failed before any mock or real dispatch; it was corrected
before input/reference freezing. The real request took 103.5s, returned HTTP 200
with finish_reason stop and reported 5,532 input plus 3,458 completion tokens.
There is no verified price or billed-cost claim.

The original observation remains failed/presales_invalid_model_output with
draft_contract; offline resolution reproduces
`responses must cover the exact requirement in order`. Strict schema, complete
input/source binding, all 17 offered spans and selected IDs pass. The top-level
conflicting_evidence status matches the reference. None creates an accepted draft.
All six response requirementText values copy necessity rules instead of the question.

Separate assistant review against 11 pre-frozen criteria passes only 3 and 6:
necessity-rule references and preservation of both authorization-conflict sides.
Failures include normative rather than affirmative propositions, unregistered
verification treated as unmet, an action assuming verification passed, an invented
certificate-submission obligation, and claims that CH-2599 evidence fills CH-2610
gaps. Conclusion/prose also contradict the met licence and training assessments.
This is not independent domain approval, a reliability estimate or public-task repair.

The original result SHA is
a3153f653869ab85b2a15c457e8558ebf67117f21c878ebac86d9ade69f059f1.
Keep its distinct presales-assessment-candidate-run-v1 identity; historical scorers
must not consume it as run-v10. No retry, output repair, reference change, embedding
or runtime integration occurred. Candidate promotion is rejected. Before further
product changes, separately investigate protocol burden versus basic primary-model
interpretation; do not add schema fields in place of semantic evidence.

Postflight records unchanged rc49/source fe995676, DB 0037, default v15/strict off,
five ready workloads, zero active jobs and unchanged source hashes, policy and
ledgers. UTC 2026-10-10 has 0 application dispatches and 2 known direct requests;
18 conservatively carried prior-day reservations yield 20/200, not 20 observed
current-day requests. Runtime sources and the v18/v19/v20 original results retain
their hashes. Product code is unchanged from fca01bb, whose 3,158 nonintegration
tests/23 subtests, Ruff, Mypy and exact-source CI passed; they were not rerun locally
for documentation-only changes. Documentation/publication checks and exact new-head
CI are recorded in the central manifest phase assessment_candidate_observation_20261010.

Six registered documentation paths use Git/new-file recovery at fca01bb. The 2,993
unrelated status entries are preserved. No disposable files or directories were
created; central evidence remains a deliverable. See
docs/ops/presales-assessment-observation-20261010.md. The parent task stays active.

## Minimal output diagnostic — 2026-10-10

MC01 uses fresh fictional CL-3106 evidence, all six prerequisite states and another
order, with a separate pre-frozen 11-point semantic reference. The input retains
full source text and applicability through prepare_citations. Only output mechanics
change to a JSON object with one answer string; no product schema or decoder changes.
The 959-byte system prompt and complete 4,569-byte request are frozen before dispatch.

The controller's first local HTTP-boundary test fails at the absent implementation,
before any dispatch. Five controlled cases then pass: streaming success, HTTP 503,
invalid answer type, non-stop completion and cancellation. Each dispatches once;
first/final state and original traces are retained, including interrupted_unknown.
These are collector checks, not model-quality evidence. Existing bounded stream
reading, response recording, credential loading and read-only preflight are reused.

The single grok-4.7/low streaming primary request returns HTTP 200/stop in 32.0s,
with 1,236 input and 1,524 completion tokens (2,760 total), response chatcmpl-4a041e53.
Billed cost is unknown. Original state is schema_valid, not an accepted product
draft. Offline checks bind the five full sources and exact request bytes to frozen
input and confirm the inspected answer is the unmodified original response.

Semantic review fails 9 of 11 criteria. It repeats the can-enable question without
answering it; says verification is unrecorded but assigns "完成扫描件读取验收";
and says training is complete but assigns "提交培训证书" without a source duty.
It cites no necessity-rule source. Both authorization-conflict sides and rejection
of other-order substitution are correct (criteria 6 and 9). Review is assistant
adjudication against pre-frozen criteria, not independent business approval.

Result SHA: 2f9c0ebe13564de846819ec065c2853d548eb00293ac04e18e8f4f8e62cae3e9.
Keep its presales-minimal-contract-run-v1 identity outside historical scorers and
public drafts. It shows these errors can occur without nested output structure;
different input and prompt prevent matched causal attribution. Do not add more
schema fields or integrate this failed diagnostic. Investigate primary inference
quality/settings next, retaining original outcomes and semantic criteria.

Fresh pre/postflight verifies unchanged rc49, source fe995676, DB 0037, v15 policy,
five ready deployments, zero active jobs and unchanged ledgers/source hashes. UTC
2026-10-10 has zero application dispatches and three known direct requests; 18
prior-day known/unknown reservations yield a conservative 21/200. Zero embedding,
fallback or deployment; no live retry or historical replay. Product code is unchanged
from 9b46e81, so no new local full-suite run is claimed. Frozen source/evidence,
documentation and exact new-head CI are checked and recorded centrally.

Recovery phase minimal_contract_observation_20261010 binds six documentation paths
to 9b46e81 or new-file absence and retains the collector/evidence. All 2,993 unrelated
status entries are preserved. No disposable resources are created or historical
files removed. See docs/ops/presales-minimal-contract-observation-20261010.md.

## Matched-input primary effort comparison — 2026-10-10

RC01 freezes one new six-state synthetic input and 11 semantic criteria for two
prospective arms before either dispatch. Both requests use the unchanged minimal
collector, complete source input and same grok-4.7 primary endpoint. The only body
difference is reasoning_effort: medium first, then low. Both keep streaming, the
120-second deadline, 4,000 requested tokens and 4,000-character answer limit. The
second input never includes the first output. This is not a retry of historical data.

The first controlled pair check fails before dispatch at absent orchestration. Four
scenarios then pass: two successful arms, HTTP 503 stopping the second, cancellation
preserving unknown and stopping the second, and preflight drift before the second.
Each actual arm is limited to one dispatch; both pending arms and every first/final
state are durably recorded. No resume/retry path is provided.

Both actual arms return HTTP 200/stop and schema_valid. Requested medium takes
32.438s, with 1,260 input and 1,831 completion tokens (3,091); low takes 24.531s,
with 1,260 input and 1,433 completion tokens (2,693). Total reported usage is 5,784;
billed cost is unknown. Returned model names are grok-4.7. The request field is
verified; provider-internal enforcement of effort is not independently attested.

Neither arm passes semantic review. Low explicitly says the unregistered test did
not pass and asks to complete it. Medium says its result is unregistered, then asks
to register a pass without first establishing it. Both add submission of a training
transcript after training is completed, despite no such rule. Overall decision,
conflict preservation and cross-order exclusion are present in both. Medium passes
criteria 1/5/6/9 and low 1/6/9. Criterion 5 narrowly checks absence of an explicit
negative inference; medium's implied positive result still fails criteria 4 and 8.
These are criterion results from assistant review, not accuracy rates or independent
domain approval. No production setting change is justified by this pair.

Result SHA: 862ff888efd8670a522639c06f4416a0c84afed70be16c6e663e038fa446eb04.
Offline binding verifies both full source projections, exact requests and unchanged
original answers. Keep presales-reasoning-comparison-run-v1 outside public drafts
and historical scorers. A single sequential pair cannot establish reliability,
universal model limits or a dominant causal explanation.

One subsequent read-only GET /models on the same primary endpoint returns grok-4.7
and glm-5.3. This is advertised availability only, with zero additional inference.
The next investigation is a separately planned same-endpoint alternate-primary-model
qualification; no switch, fallback-channel trial or further request occurs here.

Read-only postflight retains rc49/source fe995676, DB 0037, v15, five ready deployments,
zero active jobs and unchanged policy, source hashes and ledgers. Current UTC-day
application dispatches are zero; five known direct calls plus 18 carried prior-day
reservations give a conservative 23/200. Zero embedding and staging changes.
Product code is unchanged at 1444979; local validation covers the diagnostic, frozen
evidence and documentation, without a redundant local full-suite run. Exact new-head
CI, publication and recovery are recorded under reasoning_comparison_20261010.
Six documentation paths use Git/new-file recovery; the 2,993 unrelated status entries
remain unchanged. No disposable or historical files are deleted. See
docs/ops/presales-reasoning-comparison-20261010.md.

## GLM full-candidate qualification (GP01)

One prospective same-primary-endpoint request uses glm-5.3 with unchanged full
assessment-candidate.v1, low effort, streaming and a 120-second deadline. Only
model_name is overridden. Controlled success/503 verify the request boundary, one
dispatch and restored runtime identity. GP01 returns HTTP 200/stop in 34.109s with
4,132 input + 1,529 completion = 5,661 tokens; billed cost is unknown.

Original state remains failed / presales_invalid_model_output / draft_contract.
Strict schema and full source/span bindings pass; the unchanged resolver reproduces
responses must cover the exact requirement in order, because all three answers
repeat the complete requirementText. Returned z-ai/glm-5.3 differs from requested
glm-5.3; one metadata GET does not verify their canonical mapping.

Frozen criteria 2-7, 9 and 11 are satisfied in assistant review: all six states and
necessary rules, no added training proof duty, conflict sides and order scope are
correct. Criterion 1 fails coverage/identity. Criteria 8/10 are not fully satisfied:
verification is correctly unknown, but the action offers registering a passing
result without an explicit if-passed condition. This is action ambiguity, not an
incorrect unknown-state classification or an explicit assertion that the test passed.
No accuracy, independent approval or competitor-parity claim follows.

Original result SHA: d3229ad89e081a43ef97b218e4996ad0ba7593f2fffa9f14325bee0efa0de1c6.
Postflight keeps rc49/fe995676, DB 0037, default v15, five ready deployments, zero
active jobs and unchanged policy/module hashes/ledgers. UTC 2026-10-10 has zero
application dispatches and six known direct requests; carrying 18 prior-day
reservations gives conservative 24/200, not 24 actual current-day calls. No new
inference during offline review. See docs/ops/presales-glm-candidate-20261010.md.

## Server-owned question-part candidate validation

The new private question_assessment.py offers frozen literal parts, retains the full
requirement and source/span context, and accepts exactly one response per offered
ID in order. It materializes original question text before reusing unchanged
resolve_assessment. No legacy parser, gateway, settings, policy or scorer changes.
Its distinct identity is presales.question-assessment-candidate.v1.

Three incremental red/green slices first fail at the absent offering module, absent
resolver and absent provider-input/identity exports, then pass. The final 31 new
cases cover lossless bounded splitting, repeated occurrences, immutability, changed
requirement identity, missing/duplicate/reordered/foreign IDs, all four assessment
states, unchanged evidence/language/public-size rejection and separate semantic
action review. Existing assessment/span tests plus these total 114 passed.

Required local gates: Ruff format 751 files, Ruff checks, strict mypy 279 source
files, and 3,189 nonintegration tests plus 23 subtests pass (814 integration tests
deselected). Initial formatting/fullwidth-literal lint and a list annotation issue
were fixed; checks and focused tests passed afterward. The full suite ran once;
subsequent source edits only format/annotate or escape identical literal characters.
No integration/runtime deployment or model-quality success is claimed.

All earlier candidate/runtime source hashes and the seven original v18/v19/v20/AR01/
MC01/RC01/GP01 outcomes remain unchanged. GP01 is never transformed or re-scored.
No new inference, embedding, fallback or deployment occurs in this implementation.
The requested/returned GLM identity mapping and action-neutrality qualification
remain unresolved, as do public-task quality and independent user-value acceptance.

Recovery phase question_assessment_candidate_20261010 registers seven paths at
3a02c92, with Git recovery for tracked files and absent-file entries for new source
and tests. Central validation/publication/CI receipts and pre-update PR metadata
are retained. The 2,993 unrelated status entries remain unchanged; no disposable
files were created or removed, and no historical file was cleaned. The active task
and parent goal remain open.

Candidate system/schema SHA-256: 0a7968e8c538024f9f87d962205047e93453b9584e7fd98be05b44ddf5bb8799.

## QP01 live question-part observation

One new reordered six-state case succeeds in 28.157s, HTTP 200/stop, with 4,449
input and 1,806 completion tokens (6,255). All three question IDs, complete sources,
strict schema and original resolver bind; offline projection equals the saved draft.
All 11 pre-frozen semantic criteria pass assistant review for this case. Verification
action first asks whether it passed, then records the actual outcome; conflict
remediation is explicitly conditional, training adds no proof duty and other-order
evidence is excluded. This is not an accuracy estimate, independent review or a
matched causal comparison against GP01. Requested glm-5.3 returns z-ai/glm-5.3;
canonical mapping remains unverified and runtime promotion remains prohibited.

Four controlled cases pass after pre-dispatch missing-adapter/identity failures.
No product source, original result, policy or ledger is changed. Postflight keeps
rc49/0037, five ready deployments and zero active jobs. Seven known current-day
direct calls plus 18 prior-day reservations give conservative 25/200. Public
observation is separately planned/frozen; it does not expand QP01's single-call cap.
See docs/ops/presales-question-candidate-observation-20261010.md.

## CQU02 / CQU05 public question-candidate observation

Official historical procurement text was freshly fetched via Smart Search/Tavily.
Exact performance and team sections with URL, content/excerpt hashes and offsets
were frozen with both assistant-authored references before any public model call.
Only buyer rules are supplied; no supplier evidence is fabricated. Four batch
control scenarios pass: both success, first failure, interrupted first and drift
before second; the fixed adapter separately passed four HTTP-boundary checks.

CQU02 succeeds in 9.750s (2,833 input + 423 completion = 3,256 tokens). CQU05 succeeds
in 34.907s (3,845 + 584 = 4,429). Both HTTP 200/stop, original schema/bindings/resolver
and saved-draft equality pass. Requested glm-5.3 returns z-ai/glm-5.3 with mapping
unresolved. Public calls total 7,685 reported tokens; billed cost is unknown.

Semantic acceptance fails: CQU02 criterion 7 is incomplete on resource configuration
and measurement method, despite correct uncertainty, numerical targets and requests
for environment/network/load/concurrency/tools. CQU05 criteria 7/8 fail because
software project management experience becomes general work experience OR that
category; evidence requests do not reliably retain the more specific requirement.
Correct source IDs, PMP, seven-person scope and unknown states remain recorded.
No fabricated performance, people or certificates are alleged. Original succeeded
states are not relabeled; assistant review is not independent domain approval.

QP01 plus these two public calls add three requests and 13,940 reported tokens.
The final read-only postflight keeps rc49/0037, five ready deployments, zero jobs and
unchanged policies/source hashes/ledgers. Nine known current-day direct calls plus
18 prior-day reservations give conservative 27/200. No fallback, embedding, retry,
production switch or historical repair. Original deployed public replay stays
4 drafts / 2 failures / 9 calls. Scoped documentation checks replace redundant local
full-suite reruns for unchanged product code. See the question-candidate report.
