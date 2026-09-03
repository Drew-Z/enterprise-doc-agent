# Cold-Pull Evidence

## Live Observation

Collected from the staging node on 2026-09-02 03:10 CST using read-only
commands:

- The Worker Deployment was generation 21, observed generation 21, and Ready
  with one available replica.
- The Worker image event recorded a successful immutable GHCR image pull in
  `21m15.441s`, with a reported image size of `123897317` bytes.
- Its Deployment `progressDeadlineSeconds` was `600`.
- The public `https://agent.playlab.eu.cc/health/ready` endpoint returned HTTP
  200, with database, Redis, and object-store checks up.
- The current image existed in K3s containerd after the pull.

## Repository Evidence

- `.github/workflows/deploy-staging.yml` uses `rollout status` with a 600-second
  timeout for Worker during normal rollout and small-profile restoration.
- `infra/k8s/base/deployments.yaml` uses `imagePullPolicy: IfNotPresent` for
  immutable deployment images.
- `docs/ops/single-node-4c8g-staging-runbook.md` documents the existing
  checksum- and descriptor-validating OCI relay/import fallback.
- `infra/k8s/bootstrap/staging-deployer-rbac.yaml` and the matching admission
  guardrails deliberately prevent the repository deployer from receiving broad
  host or Kubernetes privileges.

## Decision

The measured delay is delivery-bound, not application-startup-bound. A
profile-specific 30-minute Worker cap preserves a failed-release signal while
the existing verified OCI relay remains the controlled fallback for unreliable
direct image delivery.
