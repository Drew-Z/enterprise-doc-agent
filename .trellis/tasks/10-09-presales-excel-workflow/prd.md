# Customer Excel questionnaire workflow

## Goal

Import a customer XLSX, confirm its question/answer columns and row range, generate and review using existing evidence, and download the answers in the original workbook. User explicitly authorized implementation on 2026-10-09.

## Requirements

- One selected worksheet; up to 120 nonblank questions; preview and explicit mapping confirmation.
- Preserve original formatting and unrelated content. Reject populated/formula/merged/protected answer targets and unsupported input visibly.
- Reuse source applicability, generation, evidence, human review history and individual failure recovery.
- Original file and mapping survive refresh/reopen. Draft export marks unreviewed and unavailable rows; reviewed export requires every question reviewed. Include effective conditions and missing materials. Keep audit CSV available.
- Import/export makes no model or embedding calls. Existing daily, batch, demo and concurrency limits remain enforced.
- Tenant/author authorization, source revocation, idempotency, bounded input/storage and redacted errors apply throughout. No file bodies in browser persistence.
- Exclude macros/encryption, arbitrary Excel features, multi-sheet aggregation, answer reuse/assignment, model routing changes and deployment.

## Acceptance Criteria

- [x] Real XLSX preview gives exact question and answer cells and skips blank question rows; malformed or occupied targets reject. No parsing mocks.
- [x] Real workbook export reopens with literal response text, conditions and missing information; unrelated XML members/content remain unchanged.
- [x] Atomic import replay/conflict, tenant isolation, limits, reload and export pass in owned PostgreSQL schemas; only provider calls are controlled.
- [x] Desktop/mobile UI exercises upload, mapping confirmation, bounded generation, review, reload and download.
- [x] Required project checks completed; prior commercial acceptance failures remain recorded and this change is not presented as real-user time savings.

## Notes

- Local acceptance completed on 2026-10-09; see `validation.md` and `docs/ops/presales-excel-workflow.md`. Deployment and real-user value remain unverified.

## Authorized continuation — 2026-10-09

The user's subsequent approval extends this task to schema 0035 release/rollback preparation, guarded staging deployment when its preconditions pass, and one sourced public questionnaire file replay. The earlier deployment exclusion applies to the completed implementation phase only.

- [x] Fixed 0034-to-0035 expansion preserves original resources and reconciles unknown outcomes without downgrading history.
- [x] Image-only 0035 switching preserves configuration; legacy readers are refused whenever workbook history exists, before and after closing admission.
- [x] Actual PostgreSQL verifies migration, interrupted receipts, exact constraints, old-reader compatibility boundary and unchanged history.
- [ ] Candidate source CI, signed artifacts, space guards and fresh staging state bind the release; application rollback retains schema 0035. Record any genuine blocker rather than bypassing it.
- [ ] Freeze a sourced public questionnaire and a bounded row range before execution; import, generate, review and return the workbook, retaining original drafts and failures. No competitor-parity or user-time-savings claim.
