# Implementation and verification

- [x] Workbook preview public behavior: real XLSX fixture, sheet/cell mapping, blank row handling; red then green.
- [x] Workbook export and unsupported inputs: preserve untouched members, literal formula-like answers, reject overwrite/protection/malformed/oversize input.
- [x] Atomic authorized persistence and migration: same-key replay/conflict, tenant isolation, demo/storage limits, restart/read/export, safe downgrade in owned PostgreSQL schema.
- [x] Authenticated API body bounds and redacted errors; no model calls for import/export.
- [x] Frontend upload and explicit mapping confirmation using existing source form; >12 rows and next-batch control, reload/review/download.
- [x] Focused unit/API/PG/UI tests, desktop/mobile browser workflow.
- [x] Repository Ruff format/check, Mypy, non-integration pytest, web lint/typecheck/test/build. Record any pre-existing failures separately without changing unrelated files.
- [x] Update presales specs and product/commercial plans; preserve historical failed acceptance. Record exact scope, recovery and verification in the existing recovery manifest.

Initial-phase recovery: D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness/manifest.json phase excel_workflow_20261009. Clean baseline files use commit 428cdef9820cf6dc926634a0a1a6578367255c81; dirty planning files use verified snapshots. New files recorded absent. The initial implementation phase made no live deployment, shared DB migration or provider calls; the later authorized continuation is recorded below. Historical workspace changes remain untouched.

## Local acceptance result (2026-10-09)

All initial implementation criteria passed locally. See `docs/ops/presales-excel-workflow.md` for the supported workflow, limits and release boundary, and `validation.md` for exact check results. Release tooling, deployment and public replay were subsequently authorized below; commercial acceptance remains separate.

## Newly authorized continuation

- [x] Red/green fixed 0035 expansion contract and original resource retention.
- [x] Red/green 0035 image switching and legacy-reader history guards at both race boundaries.
- [x] Real owned PostgreSQL migration/receipt/constraint tests and retained workbook roundtrip.
- [x] Required checks, exact scoped commit, candidate CI and signed-artifact preparation.
- [x] Fresh live preflight, sufficient cache capacity, supervised migration and deployment/rollback when guards pass.
- [x] Public-source freeze, bounded complete selected-range workbook replay, original/model/review separation and final delivery.

Recovery continues in the existing central group, phase `excel_release_20261009`. The approval extends the previous local-only boundary; commercial acceptance and historical workspace changes remain untouched.

Final result: release/rollback/reapply and file preservation passed; six-row generation ended with four drafts and two failures, nine calls and no manual retries. Four assisted reviews preserve originals and produce a partially reviewed draft workbook. Quality failures remain open in `docs/ops/public-excel-replay-20261009.md`; no further repair batch was started.
