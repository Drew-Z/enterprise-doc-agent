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

- Local acceptance completed on 2026-10-09; see `validation.md` and `docs/ops/presales-excel-workflow.md`. Deployment was subsequently verified in the authorized continuation below; real-user value remains unverified.

## Authorized continuation — 2026-10-09

The user's subsequent approval extends this task to schema 0035 release/rollback preparation, guarded staging deployment when its preconditions pass, and one sourced public questionnaire file replay. The earlier deployment exclusion applies to the completed implementation phase only.

- [x] Fixed 0034-to-0035 expansion preserves original resources and reconciles unknown outcomes without downgrading history.
- [x] Image-only 0035 switching preserves configuration; legacy readers are refused whenever workbook history exists, before and after closing admission.
- [x] Actual PostgreSQL verifies migration, interrupted receipts, exact constraints, old-reader compatibility boundary and unchanged history.
- [x] Candidate source CI, signed artifacts, space guards and fresh staging state bind the release; application rollback retains schema 0035. Record any genuine blocker rather than bypassing it.
- [x] Freeze a sourced public questionnaire and a bounded row range before execution; import, generate, review and return the workbook, retaining original drafts and failures. No competitor-parity or user-time-savings claim.

Continuation completed: rc46/0035 released, actual rc45 rollback/reapply verified before workbook history existed. Six derived public SWU rows produced four drafts and two failures in nine calls, with four assisted reviews and a partial draft workbook export. This completes the bounded file-workflow task, not answer-quality or commercial acceptance; see `validation.md`.

## Authorized focused remediation

The user approved the three follow-up priorities. Preserve the completed rc46 replay and its failures. Add safe failure diagnostics, fix demonstrated Chinese keyword recall, and clarify same-scope support in the model instruction; do not add routes, retries, budget or platform features.

- [x] Chinese procurement requirements retrieve the matching clause through real PostgreSQL even when the controlled vector boundary contributes no match; tenant/version/active-generation filters and top-k limits remain.
- [x] Failed output records a fixed diagnostic category without raw model/source text, arbitrary exceptions or new schema; synchronous and durable executions preserve exactly-once accounting and normal fallback bounds.
- [x] Different edition/time/entitlement is explicitly excluded as same-scope counterevidence; existing missing-evidence fixtures preserve unknown. This verifies the instruction and controlled decoder contract only, not live model scope reasoning.
- [x] Prepare exact scoped candidate and checks; preserve historical outputs and state which reliability/semantic issues remain unproven.

Implementation checks passed; the bounded live diagnostic lost its control receipt and remains unknown, with at most three calls conservatively reserved and no rerun. Publication/CI receipt is retained centrally. The candidate is not deployed and actual quality remediation remains open; see `docs/ops/public-replay-remediation-20261009.md`.

## rc47 release continuation

The focused candidate is now deployed on staging. Actual rc46 rollback/reapply, retained workbook history and deployed SWU03 keyword recall passed without new model/embedding calls. Historical quality failures and the unknown diagnostic remain. Human takeover for pending/failed rows is the next delivery gap; current implementation still requires a model draft before review/export. See `docs/ops/rc47-remediation-release-20261009.md`.
