# Local verification — 2026-10-09

The Excel workflow is implemented and locally verified. No staging deployment, shared-schema migration, remote push, or live model/embedding calls were made.

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

Next: prepare schema 0035 candidate publication and application rollback compatibility, then replay one sourced public customer questionnaire through the complete file workflow. This verification does not establish competitor parity, commercial acceptance, user time savings or willingness to pay.

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

Fresh read-only staging observation remains rc.45/0034, five workloads ready, idle
business, unchanged credential fingerprints and approximately 14.41 GiB free. This
is a preflight, not a live schema upgrade or release. Evidence is in the central task
group: `excel-release-preflight.json` and `excel-release-pytest-nonintegration.log`.
Candidate publication and public file replay remain pending at this commit.
