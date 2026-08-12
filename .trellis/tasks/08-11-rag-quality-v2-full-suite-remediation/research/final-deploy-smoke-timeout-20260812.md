# Final Deploy Smoke Timeout Diagnosis

## Scope

This note records only safe operational facts from the final remediation deployment attempts.
It does not include JWTs, credentials, object keys, runtime identifiers, document content, or
provider payloads.

## Attempt 1

- Deploy run: `31550723096` at commit `7c585e59a32153e397258ba45605fd3f040e0cfc`.
- Migration, rollout, and embedding rollout passed.
- The one-shot in-cluster readiness Job failed before the authenticated smoke.
- A follow-up Pod using the same image, labels, NetworkPolicy path, and readiness URL returned
  HTTP 200 with database, Redis, and object-store checks up.
- The failed workflow artifact is preserved under
  `tmp/deploy-staging-31550723096-failed/`.

## Attempt 2

- Deploy run: `31551145786` used the same commit and four immutable image digests.
- In-cluster readiness passed.
- The authenticated smoke created the upload, obtained a presign, uploaded the fixture, and
  completed the upload successfully.
- Document ingestion completed successfully in `62.84` seconds.
- The concurrent authenticated ready-version request completed successfully on the server in
  `63.10` seconds, after the smoke client had failed its fixed 30-second request timeout.
- A separate authenticated measurement returned HTTP 200 in `30.86` seconds, confirming that the
  30-second transport budget was below observed healthy staging latency.

## Decision

- Keep the existing 300-second end-to-end smoke deadline.
- Raise only `UrlLibSmokeClient.timeout_seconds` from 30 to 90 seconds.
- Do not change API behavior, data, retrieval, prompt behavior, model routing, quality thresholds,
  or the immutable runtime digests for this transport correction.

## Attempt 3

- Deploy run: `31552350141` used source commit `25aa1b69ae80ca8b33d684cbfa97715020233fec`
  and the same four immutable runtime digests.
- Migration, rollout, embedding rollout, readiness smoke, upload, ingestion, and ready-version
  polling passed.
- The Agent run did not reach a terminal status inside the existing 300-second end-to-end smoke
  deadline.
- Safe durable status showed attempts 1-4 as retryable `mcp_client_timeout`; attempt 5 was still
  running at collection time. The public Agent run had no terminal error code yet.
- Tool projection showed `search_document` succeeded in `17.91` seconds and
  `create_draft_artifact` remained running. The Worker-side 30-second MCP stdio deadline ended the
  client before the object-store write/readback/database-finalize operation could return a stable
  MCP result, so no allowlisted diagnostic subcode was available.

## MCP Decision

- Keep `AGENT__EXECUTION_MAX_ATTEMPTS=5` and the smoke's 300-second end-to-end deadline unchanged.
- Set `MCP__REQUEST_TIMEOUT_SECONDS=90` in the staging ConfigMap. The same setting configures the
  Worker client, MCP server operation deadline, and stale execution recovery window.
- Do not weaken tool authorization, artifact integrity verification, idempotency, or retry
  classification.
