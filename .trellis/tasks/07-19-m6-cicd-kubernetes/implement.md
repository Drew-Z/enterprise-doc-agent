# M6 CI CD Kubernetes And Rollback: TDD Implementation Plan

## Slice 0: Contracts And Safe Runbooks

- [x] Add M6 Trellis contracts and external-gate rules.
- [x] Add Docker/Kubernetes/workflow contract tests and safe backup/rollback CLI help.

## Slice 1: Images

- [x] Add API, Worker/consumer and Web multi-stage Dockerfiles, nginx config and
  `.dockerignore`; pin runtime image digests where available.
- [x] Ensure non-root runtime and no source secrets in image layers; actual registry build remains
  an external execution gate.

## Slice 2: Kubernetes Base And Overlays

- [x] Add namespace/config/secret references, Deployments, Services, migration Job,
  probes, resources, PDB, RBAC and NetworkPolicy.
- [x] Add staging/prod overlays with image digest placeholders and explicit replicas.

## Slice 3: CI/CD And Supply Chain

- [x] Add image build/push, scan, SBOM, provenance, staging deploy, promotion and
  rollback workflows with manual environments.
- [x] Keep remote credentials and cluster operations as external gates.

## Slice 4: Backup/Restore And Release Records

- [x] Add non-destructive backup, restore-confirmation and kubectl rollback scripts.
- [x] Emit sanitized release/rollback records with digest, migration revision, RPO/RTO
  and operator fields.

## Slice 5: Evidence And Documentation

- [x] Render manifests, run static contracts, run available local image/compose checks,
  and record blocked external gates honestly.
- [x] Update README/spec and interview Q&A with real deployment boundaries.

## Completion Rules

No M6 task is archived from static files alone when the parent requires staging,
immutable image or rollback evidence. Those remain open manual gates.

## Slice 6: Tiny Single-Node K3s Staging

- [x] Add a rendered-contract test for final resources, Traefik ingress, external
  durable dependencies, Redis policy, and the 2C2G resource ceiling.
- [x] Add the `tiny-single-node` overlay with one replica per service, no PDBs, a
  bounded ephemeral Redis delivery layer, and explicit network boundaries.
- [x] Parameterize staging deployment over a strict profile allowlist and persist the
  selected profile in the release record.
- [x] Run the full deployment test suite and Kustomize/workflow validation locally;
  keep registry, cluster apply, smoke, rollback and recovery evidence external.

## Slice 7: Independent Review Hardening

- [x] Give the smoke Pod a staging-only NetworkPolicy identity and final-render test.
- [x] Include migration overlap in the 1 GiB budget and disable rollout surge.
- [x] Guard Namespace profile ownership before apply and hash profile evidence.
- [x] Restrict PostgreSQL egress to database-using Pods and one validated host CIDR.
- [x] Edit image digests in the staging parent overlay and regression-test the final tiny
  images with Kustomize 5.7.1.
- [x] Replace the remotely broken Trivy composite action with a revision whose nested
  setup/cache actions are SHA-pinned, and lock the scanner version explicitly.
