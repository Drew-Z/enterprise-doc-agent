# Design: Bounded Worker Image Delivery for 4C4G Staging

## Problem

The dedicated staging node can eventually pull the immutable Worker image from
GHCR, but its measured cold pull (1275 seconds) exceeds the deployment and CI
progress limit (600 seconds). Kubernetes then reports deployment progress
failure even though containerd continues the pull and the Worker later starts.

## Selected Design

Use two explicitly bounded delivery paths:

1. Direct GHCR pull remains the normal path. Only the 4C4G Worker receives a
   1800-second Deployment progress deadline and matching workflow rollout
   timeout. This is long enough for the measured pull plus startup time, but
   remains finite and surfaces a failed release.
2. The existing OCI relay/import path is the fallback after a direct delivery
   failure or a known GHCR reachability problem. It transfers the exact
   digest-preserving OCI archive through the reviewed relay, validates the
   archive and OCI descriptors, and imports it into the K3s containerd image
   store. `IfNotPresent` then lets the standard immutable Deployment use the
   imported image without a new direct pull.

## Boundaries

- Kubernetes manifest change: the `single-node-4c4g` overlay owns the Worker
  progress-deadline override. The base manifest keeps its generic behavior.
- Workflow change: `deploy-staging.yml` owns the matching bounded waits for
  initial rollout and restoration. It does not gain containerd or host-root
  access.
- Documentation change: the 4C4G runbook owns the operator decision tree and
  refers to the canonical 4C8G relay section rather than duplicating a
  security-sensitive import procedure.
- Existing `Relay Staging Images` and `import_staging_oci_archive.py` remain
  the authoritative transfer and verification mechanism; this task does not
  redesign them.

## Compatibility

The change is limited to the 4C4G overlay. Other profiles retain their current
Kubernetes default or reviewed deadlines. Workload image names, digests,
resource limits, deployment permissions, and rollout sequence are unchanged.

## Failure and Rollback

An unavailable Worker after 1800 seconds is still a failed staging deployment.
The workflow's existing `always()` restore step reapplies the expected 4C4G
workloads. Operators then inspect its sanitized evidence, choose the existing
OCI relay path, and dispatch a new deployment only after verifying the import
receipt. Reverting this task restores the Worker-specific deadline and the
former 600-second workflow wait; it does not remove imported images.

## Security Rationale

Automating a node-level pre-pull from the GitHub runner would require broader
host privileges than the current deployer boundary permits. The selected design
preserves repository-scoped, least-privilege Kubernetes credentials and moves
the exceptional image import to an explicit administrator action with checksum
and descriptor validation.
