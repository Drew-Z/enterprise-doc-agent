# Close external delivery gates

## Goal

Prepare the repository to produce auditable external delivery evidence, while
keeping the distinction between local verification and cloud-dependent facts.

## Requirements

- The tagged container workflow must scan, attest, sign, and verify the exact
  immutable image digest that it publishes.
- Supply-chain evidence must remain available when a scan or verification step
  fails, without weakening the failure gate with global allow-failure behavior.
- The Web image must receive an explicit object-store origin allowlist at build
  time so browser uploads work against HTTPS staging presigned URLs.
- Staging manifests and workflow contracts must support ingress/TLS, runtime
  secrets, immutable image digests, migration-before-workload ordering, and
  redacted deployment evidence.
- Recovery and capacity evidence formats must require environment identity,
  commit/image identity, timings, measurements, limitations, and an explicit
  blocked_external state when cloud execution did not occur.
- No real credentials, private keys, or production endpoints may be committed.

## Acceptance Criteria

- [ ] Focused workflow and Docker tests prove final-digest binding, attestation
      verification, evidence upload behavior, and Web object-origin injection.
- [ ] Staging Kustomize renders and workflow YAML contracts pass locally.
- [ ] Full repository quality and test gates pass.
- [ ] A private GitHub remote and real Actions/GHCR run are created only when an
      accessible repository target is available; otherwise the blocker is
      recorded with exact prerequisites.
- [ ] Any real staging, recovery, rollback, or capacity run includes immutable
      evidence and measured results; absent external infrastructure remains
      blocked_external rather than passed.

## Constraints

- Do not deploy to production or run destructive restore commands.
- Do not commit secret values or embed environment-specific credentials in
  manifests.
- Preserve existing local evidence and unrelated user changes.
