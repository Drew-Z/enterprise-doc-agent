# Technical Design

## Boundaries

Add one standalone Python CLI under `scripts/` and one focused contract test
module. The script imports the existing `UrlLibSmokeClient` and endpoint
validation helper from `scripts/staging_smoke.py` so HTTPS, host allowlists,
redirect rejection, request timeout and authorization headers stay consistent.
No API or database schema changes are required.

## Scenario

The owner token creates a one-part synthetic text upload and captures only the
opaque document/grant/binding IDs in memory. The document is switched to
`restricted`; a member user grant is created and the member inventory is
checked for one additional visible document. The grant is removed and the
member inventory is checked again. The report records only boolean outcomes and
bounded counts.

The owner then updates retention to a short-lived, enabled policy, creates a
unique legal hold, calls preview and plan, and releases the hold in a `finally`
path. If the plan has eligible events, the script archives a bounded batch and
verifies it; if not, it records `archive_skipped_no_eligible_events`. Existing
events are never deleted.

For identity binding, the owner lists active members, selects the first member
user ID, creates a unique synthetic issuer/subject, deactivates and reactivates
the binding, and confirms the final list. The issuer/subject values are never
written to the report.

## Failure and cleanup

Every HTTP response is checked against an explicit status set and converted to a
stable step failure. A failed mutation attempts only safe compensating cleanup:
release the legal hold and deactivate a created binding. It does not delete
documents or audit events because those APIs are intentionally append-only or
not available. The report is written only after sanitizing the in-memory step
records.

## Workflow integration

`deploy-staging.yml` gains a protected environment variable
`STAGING_RUN_GOVERNANCE_SMOKE=true` and a step after the existing authenticated
smoke (the workflow already uses the GitHub ten-input limit). The step uses
`STAGING_GOVERNANCE_OWNER_TOKEN` and `STAGING_GOVERNANCE_MEMBER_TOKEN` from the
`staging` environment, plus non-secret host variables already used by the main
smoke. Sanitized governance JSON is included in the existing evidence directory
and the release record has a separate governance outcome field. Kubernetes
credentials and RBAC remain unchanged.

The gate is opt-in so historical deploys remain reproducible; a run that opts
in and fails must fail the workflow. Formal promotion still requires a passed
governance record from a dedicated tenant.
