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

## Focused remediation after user approval

- [x] Red/green actual PostgreSQL Chinese recall and isolation tests.
- [x] Red/green gateway safe diagnostics and durable/synchronous persistence checks.
- [x] Scope-aware prompt revision; controlled positive/negative/unknown cases retain semantics.
- [x] Required local checks, scoped candidate preparation and honest evidence report. Exact publication/CI is recorded in the central recovery group.

Recovery phase: public_replay_remediation_20261009 in the existing central group. Original rc46 results are immutable.

Candidate verification: 2,920 non-integration tests / 23 subtests, 3 new real PostgreSQL tests, Ruff and Mypy passed. A bounded live diagnostic lost its SSH control receipt; outcomes are unknown, three calls remain conservatively reserved, and no repeat was made. The exact orphan was stopped and unchanged rc46/0035 identity, original replay ledger and readiness independently rechecked. Prompt quality and deployment remain open, distinct from the completed Excel milestone.

## rc47 deployment result

- [x] Exact-source signed candidate, capacity guard and complete OCI preservation passed.
- [x] Actual rc47 release, compatible rc46 rollback and reapply; all three windows passed22 independent checks including workbook history.
- [x] Deployed keyword query retrieves the previously missed two clauses first, with zero new provider calls.
- [x] Owned temporary resources cleaned; signed evidence and original failures retained.
- [ ] Real-model completion and semantic quality verified.
- [x] Human response entry, evidence, review and export for rows without a model draft (local candidate; staging rollout remains separate).

## Human completion slices

- [x] Real PostgreSQL service/API: pending manual response, literal evidence, immutable authorship, replay, separate review, original XLSX/CSV; fail before implementation.
- [x] Active/terminal generation, stale revision, competing saves, invalid/foreign/stale evidence and revoked membership; no new calls/reservations; migration history refusal.
- [x] Typed browser evidence selection/manual form, explicit provenance, review/reload/download at desktop/mobile; controlled external boundaries only.
- [x] Full affected-package checks, recovery hashes and current limitations. Scoped publication recorded in central manifest.
- [ ] Guarded0036 expansion/reader-compatible release. Staging remains rc47/0035 until that verified window.
