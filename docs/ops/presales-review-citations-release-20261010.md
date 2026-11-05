# Review citation release tooling — 2026-10-10

Status: signed rc49 is deployed on schema 0037. Expansion, actual pre-history rc48
rollback/reapply, and a new human citation-correction delivery fixture passed.
The local-tooling checkpoint below is retained; executed results follow at the end.

An earlier candidate adds independent citations to each human review. rc48 cannot
interpret that side column, so restoring it after a correction would show the wrong
evidence. The release executor now treats citation history as a separate compatibility
boundary alongside workbook and human-authorship history.

## Changes and recovery

- Fixed0036-to-0037 SQL must match the committed Alembic migration. Expansion retains
  every original application/configuration/credential resource and both inherited
  reader capabilities. The single private psql session and advisory lock survive the
  transaction, with verified limits and the existing immutable deadlines.
- Exact inherited shapes and the nullable/default-free citations JSONB array constraint
  are checked. Unknown commit receipts reconcile either complete revision without DDL
  replay or downgrade. Recovery refuses citation history before writes and before
  reopening old applications, and protects inherited workbook/manual history too.
- Schema0037 image plans require explicit boolean original/candidate capabilities for
  all three histories. Apply checks both readers and restore checks the original reader,
  before writes and again after apps stop. Existing idle and complete-resource guards
  remain. An explicit empty array counts as history; only null retains draft binding.
- The initial rc48 rollback drill is possible only before independent citation history.
  Afterwards use compatible images or a forward fix. Never erase reviews or authorship
  to make old readers eligible. Existing human history already excludes rc47/rc46.

## Local verification

Deployment nonintegration:1,367 passed,50 deselected (163.60s). All four real PostgreSQL
expansion suites:50 passed (131.69s), including15 new citation cases. Actual records
cover null/empty/nonempty citations, prior reviews, human drafts, unchanged XLSX/CSV and
zero gateway/accounting changes through compatible or refused recovery. Other cases
cover atomic DDL failure, lost commit receipts and all inherited/new schema drift.

Ruff check/format passed739 files. Mypy passed274 application sources and both changed
deployment scripts on their Linux target. An initial Windows-target script check flagged
the existing Linux-only host-clock branch; no ignore or unrelated source change was made.
The complete Python nonintegration suite passed3,029 tests and23 subtests, with810
deselected (289.55s). Product/frontend code is unchanged from candidate7cc745a, whose457 Web
tests and desktop/mobile browser acceptance were already verified.

## Scope and evidence

No live model/embedding calls, new routes/retries/budgets, frozen replay mutations,
shared-schema changes or staging operations occurred during local tooling validation.
Original public results remain4 drafts/2 failures/9 calls. The unknown diagnostic remains
unknown. Human evidence correction does not prove model accuracy, real-user time savings,
paid demand, independent business approval or full competitor parity.

Central recovery group:
`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness`.
Phase:`review_citations_release_20261010`; baseline:`7cc745ac937dd50c275d0b12d464bb26e55c5f77`.
Tracked clean sources use that commit as their verified restore point; newly introduced
files were registered as absent. No additional source-side backup copies. The2,993
unrelated Git entries must retain their original normalized status hash. Owned test
schemas/temporary resources are removed by fixture cleanup; no historical images,
business data, backup groups or uncertain workspace files are deleted.

Publication continues with exact-source CI and signed artifacts, a fresh live identity /
idle / history / capacity preflight, guarded expansion, image release/rc48 rollback/reapply,
and a new explicitly labeled human-only citation correction/reload/export fixture.
Frozen public samples and all earlier failed results remain unchanged throughout.

## Executed staging release and delivery

Application `fe995676649570328892e2b48d80f02ee9526dd8`, tag `v0.1.45-rc.49`:
Quality 37981892144, Container 37981892045 and signed build 37982424490 passed.
Five artifacts and 56 evidence files were verified against the exact source.

The first full-batch capacity check refused import before target writes, with an
836,391,680-byte shortfall. The user approved exactly 32 cache references for eight
rc36–37 images, plan SHA256
`ebef92bb32eac33dfeb2b64d7c695a7e14512ecc073b1429b7bb75cb83017b40`.
Independent verification confirmed only those references disappeared, every protected
reference remained, and net free space increased by 2,214,735,872 bytes (2.06 GiB).
The unchanged aggregate guard then passed. The complete signed archive was imported;
no split-import workaround or reduction of eviction/reclaim reserves was used.

The live session-pooler lock checks passed. Schema expansion took 74.649 seconds;
release / rc48 rollback / rc49 reapply took 85.770 / 83.024 / 86.277 seconds.
All 28 schema checks and 22 checks for each image window passed. Exact resources,
credentials, packaged sources/assets, frozen workbook, existing human history,
accounting and service readiness were independently checked.

One newly labeled workbook passed public HTTP preview/import, a human draft deliberately
retaining an irrelevant historical quote, a legacy review, then a separate corrected
review with the authorized certificate/undertaking clause. GET retained the original
draft, authorship and both review snapshots. Reviewed XLSX and CSV used corrected content
and evidence while preserving original quotations, the formula and the unrelated sheet.
All six global job/attempt/call/reservation/dispatch counts remained unchanged; zero new
model or embedding calls. The frozen public packet and previous human packet are unchanged.

The initial harness stopped before workbook creation: it confused external sample label
SWU03 with persisted imported key X7. Read-only diagnosis proved no owned packet, no
citation history and unchanged accounting. The corrected harness binds key, exact question
and B7/C7 location. Preserve the initial failure; no uncertain write was retried. All
temporary test tokens were revoked, including the failed and diagnostic runs.

Independent citation history now exists. Actual `ReleaseCluster.restore` refused rc48
before any Kubernetes write through a read-only boundary. The earlier rc48 rollback
drill is no longer an eligible recovery path. Use citation-compatible images or a forward
fix; never delete review history to reopen legacy readers.

Removed only 233 verified remote temporary files / 315 directories and 43 local transport
files / one directory. Approved cache cleanup is separately accounted above. Signed
artifacts, central recovery evidence, business history and unrelated workspace files remain.
Post-cleanup identity checks found five ready services; homepage and readiness returned 200.

Receipt hashes: [rc49 validation](rc49-citation-release-validation.json). Documentation
recovery uses commit `fe995676649570328892e2b48d80f02ee9526dd8` in the same central group.
Spec review: existing 0037/history/capacity contracts already cover this release; the
fixture-key correction changes no product contract. Documentation-only closeout validates
JSON, evidence hashes, task contexts and scoped diff; unchanged code suites are not repeated.

This completes the citation-correction release milestone. Original model results remain
4 drafts / 2 failures / 9 calls. Model completion reliability and semantic evidence quality,
real-user benefit and independent business acceptance remain open; competitor parity is
not established. Keep answer reuse, assignments and new channels deferred.
