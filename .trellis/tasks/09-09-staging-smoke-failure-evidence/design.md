# Staging smoke failure evidence: design

## Boundary and flow

Keep the existing client protocols and exception-based library API. An internal progress object owns start time, completed steps, current step, optional canonical Agent status and hashes of observed identifiers. The execution path updates that object only after the corresponding operation is confirmed. Artifact list/download/hash/citation checks mark their own completion independently.

`run_staging_smoke` attaches a sanitized report to `StagingSmokeFailure` and re-raises. It converts unexpected execution errors to a bounded failure code at this script boundary, without serializing their messages. `main` renders that report, attempts the requested file write, prints JSON, and exits 1 on any failure. Configuration failures after argparse has succeeded use an empty-progress report. Broken CLI syntax and OS process termination remain outside report guarantees.

## Report contract

Schema version 2 retains `scenario`, `status`, `steps`, `sample_count`, `duration_seconds`, `started_at`, `completed_at`, and limitations. Add `correlation_sha256` (only observed upload session, document version, Agent run and artifact references) and `agent_terminal_status` (canonical terminal enum or null). A failed report adds `failure` with current step, finite code and nullable HTTP status. Missing provider diagnostics remain unknown and no raw diagnostic/body is copied. `sample_count` counts confirmed Agent run creation, including a failed run; it is not provider request count.

Use fixed internal failure codes for invalid configuration, HTTP error, transport failure, timeout, unexpected execution failure, non-success Agent termination, contract validation and report-write failure. Existing exception messages remain available to in-process callers but are not a report or CLI logging input. HTTP status is copied only as a valid numeric status, never inferred from response text. On a failed output write, add `report_output` with `report_write_failed`; preserve any existing business failure, otherwise fail the report at `report_write`. Stdout retains the resulting JSON.

## Compatibility and security

The schema increment labels the newly supported failed-report contract. Success still raises no exception and follows the existing one-upload/one-QA/one-artifact path. Imported clients and `StagingSmokeFailure(message)` remain compatible. Workflow outcome-based release acceptance is unchanged; its `always()` collector already retains this file. No extra API calls, cleanup mutations or retries are added.

The only public correlation values are SHA-256 digests; do not add original IDs even for synthetic data. File-write errors print only a fixed error label and leave the JSON available on stdout. Existing evidence files are never rewritten.

## Validation and rollback

Use fake clients and transport boundaries for deterministic failure injection. Exercise CLI output/exit and the artifact pipeline, rather than asserting implementation text. Run focused related consumers and release tests, then required non-integration/lint/type checks. Rollback consists of reverting this child's changed code and spec after a future commit; no schema/database/remote rollback is required.
