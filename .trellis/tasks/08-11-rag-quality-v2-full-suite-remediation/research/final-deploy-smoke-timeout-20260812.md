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
