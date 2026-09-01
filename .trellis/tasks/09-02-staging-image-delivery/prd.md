# Harden Restricted-Network Staging Image Delivery

## Goal

Make the `single-node-4c4g` staging release path tolerate a slow first pull of
an immutable GHCR image without weakening image immutability, the deployment
service account, or the single-node resource contract. The `v0.1.33` candidate
must be promoted only after a controlled rollout has completed its existing
embedding, readiness, and authenticated smoke gates.

## Confirmed Facts

- The Guangzhou staging node has 4 vCPU, 3.6 GiB usable memory, no Swap, and
  26 GiB free disk. At 2026-09-02 03:10 CST, all five long-lived staging
  deployments were Ready and the migration Job was Complete.
- The public readiness endpoint returned HTTP 200 at that time.
- The Worker image for the release candidate is immutable and uses
  `imagePullPolicy: IfNotPresent`.
- The first Worker pull completed in 21 minutes 15 seconds, while both the
  Worker Deployment and the staging workflow used a 600-second progress limit.
  The container subsequently started and became Ready, so the observed failure
  is a delivery-timeout defect rather than a Worker startup defect.
- The repository already has a checksum- and descriptor-validating OCI relay
  workflow and receiver for restricted networks. The 4C4G runbook does not
  currently point operators to that path.

## Requirements

### R1: Bounded Direct-Pull Release Path

For `single-node-4c4g`, give a Worker image cold pull a bounded 30-minute
window at both the Kubernetes Deployment and GitHub staging-workflow layers.
Keep the existing 10-minute budget for the other application Deployments.

### R2: Observable Failure and Fallback

When the Worker cannot become available during its bounded window, the workflow
must retain its existing diagnostic and rollback behavior and fail the release.
It must not retry indefinitely or silently declare the release healthy.

The documented operator fallback is the existing `Relay Staging Images`
workflow followed by its checksum-verified OCI import. It remains a deliberate
administrator-owned prerequisite, rather than granting the repository runner
host-root image-import privileges.

### R3: Immutable, Least-Privilege Delivery

All delivery paths must retain exact `@sha256` references and
`imagePullPolicy: IfNotPresent`. Do not add mutable tags, registry credentials
to the repository, broad Kubernetes permissions, or passwordless host-root
access for the GitHub Actions runner.

### R4: 4C4G Operational Documentation

Document the relay decision point and the exact supported 4C4G procedure,
including the requirement to validate the receipt before importing and to
preserve non-secret evidence. Do not copy signed URLs, credential material, or
other sensitive values into the repository or deployment reports.

### R5: Release Validation

After the code and runbook change, rerun the controlled `v0.1.33-rc.2`
staging workflow with its immutable digests. It must pass migration, workload,
embedding/reindex, in-cluster readiness, and authenticated upload-to-Agent
smoke gates before public UI acceptance and promotion are considered.

## Acceptance Criteria

- [ ] AC1: The rendered `single-node-4c4g` Worker Deployment declares a
      1800-second progress deadline; other application Deployment deadlines
      remain unchanged unless separately justified.
- [ ] AC2: The staging workflow waits up to 1800 seconds for the Worker in
      normal rollout and small-profile restoration paths, while API, Consumer,
      and Web remain at 600 seconds.
- [ ] AC3: Deployment contract tests assert the profile-specific deadline and
      bounded workflow behavior, and pass alongside the existing test suite.
- [ ] AC4: The 4C4G runbook links the existing verified relay/import procedure
      and clearly states when it is required after a direct-pull failure.
- [ ] AC5: No Kubernetes RBAC/admission-policy expansion, secret disclosure,
      mutable image reference, or runner sudo grant is introduced.
- [ ] AC6: A new controlled staging run completes all existing workflow gates;
      the resulting sanitized evidence is reconciled with `v0.1.33-rc.2`.

## Out Of Scope

- Adding host-root permissions to the self-hosted runner.
- Replacing GHCR, operating a permanent pull-through registry, or adding a
  second staging node.
- Changing the approved application image digests or model/embedding settings.
- Claiming high availability, production capacity, or an automated recovery
  drill from this single-node validation.

## Open Questions

None. The bounded 30-minute Worker window plus the existing verified relay
fallback is selected because it addresses the measured 21-minute cold pull
without turning an external-registry failure into an unbounded wait.
