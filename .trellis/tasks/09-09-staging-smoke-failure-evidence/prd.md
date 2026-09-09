# Staging smoke failure evidence

## Goal

Make a failed authenticated upload→ingestion→Agent smoke reviewable through the same sanitized JSON artifact as a passed smoke, while continuing to fail the deployment gate. Owns a bounded implementation of M6-R4 and parent DR-7/DR-10.

## Confirmed Facts

- Baseline `ffca3d0861ae22c857fef54260b9fc4c083e980e`: `scripts/staging_smoke.py:494` rejects non-success terminal states; `main` at line 553 writes a report only after normal return.
- `.github/workflows/deploy-staging.yml:643` collects evidence with `always()` and copies an existing smoke report at line 695. `scripts/build_staging_release_record.py:335` derives acceptance from workflow outcomes.
- Run `34248230396` failed in Agent execution with no smoke JSON. The [diagnosis handoff](../07-19-m5-observability-eval-load/research/20260909-v0.1.34-smoke-diagnosis.md) keeps the underlying cause unresolved.
- `SmokeClient`, `UrlLibSmokeClient` and `StagingSmokeFailure` are also imported by governance smoke and staging evaluation; preserve those calling contracts.

## Requirements

- **SFE-R1**: After valid CLI parsing, both success and failure produce schema-versioned JSON on stdout and at `--report-path` when writable. Failure retains exit code 1. File-output failure is itself non-success and cannot erase the stdout report.
- **SFE-R2**: Record only completed step names, the failing step, a bounded failure code, optional HTTP status, canonical Agent terminal status, UTC timings, and SHA-256 references for identifiers actually obtained. Count only confirmed Agent-run creation in `sample_count`; unknown provider requests/cost/diagnostics are not fabricated.
- **SFE-R3**: Never serialize raw exception messages, HTTP bodies, prompts, artifacts, signed URLs, bearer tokens or raw identifiers. Unknown response status and missing diagnostic details cannot become arbitrary report text or a guessed cause.
- **SFE-R4**: Preserve `run_staging_smoke` failure exceptions, success checks, exact one-upload/one-QA request sequence, HTTPS/allowlist behavior, time budgets and artifact/citation validation. Attach the report to failures for the CLI; do not retry or add diagnostic API calls.
- **SFE-R5**: Offline regressions cover Agent failure, ingestion/run timeout, early upload/HTTP failure, incomplete or malformed responses, artifact failure, configuration failure, writable/unwritable report output, and unchanged successful execution. Related governance/evaluation and release-gate checks remain green.

## Acceptance Criteria

- [x] R1/R2: A synthetic Agent failure exits 1, writes parseable failed JSON with the correct completed steps, terminal state and hashed references, and never claims artifact validation.
- [x] R2/R3: Early upload failure and timeout report only observed progress; unsafe exception/response values do not appear in output or the report.
- [x] R1/R4: Configuration/output failures remain nonzero; successful smoke keeps every prior acceptance check and request count.
- [x] R4/R5: Focused smoke/related caller/release tests, full non-integration tests, Ruff and mypy pass with no external provider calls.
- [x] Parent queue, task validation record and adopted spec describe the actual implementation and next child.

## Out Of Scope

Remote mutation, deploy/rerun/rollback, new real RAG trials, token rotation, Worker process identity changes, retrospective root-cause claims, or marking v0.1.34 accepted. Existing 86-file evidence archive remains unchanged. Commit and archive await the separately required concrete commit confirmation.
