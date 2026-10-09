# Review citation release tooling — 2026-10-10

Status: fixed0036-to-0037 expansion and citation-reader guards implemented locally.
Signed publication and live release acceptance are pending at this checkpoint.
Last verified staging is rc48/0036; this report does not claim a live0037 migration.

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
