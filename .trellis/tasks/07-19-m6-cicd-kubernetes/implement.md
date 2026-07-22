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

## Slice 8: Real Staging Model Routing

- [x] Fail closed unless staging uses a reviewed OpenAI-compatible HTTPS `/v1` route and
  exact model identifier, with the API key kept only in the Kubernetes Secret.
- [x] Keep workflow dispatch within GitHub's ten-input limit by sourcing non-secret model
  routing from protected staging Environment variables.
- [x] Hash model configuration into every backend and migration Pod template and retain
  the sanitized route metadata in the release record.

## Slice 9: Scoped Staging Bootstrap And Atomic Rollback Preflight

- [x] Move Namespace, ConfigMap, ServiceAccount, Service, Ingress, NetworkPolicy and PDB
  mutation behind an administrator prerequisite boundary; keep the deployer to reviewed
  Deployments, Jobs and read-only diagnostics.
- [x] Record approved endpoints, model routing, prerequisite fingerprint and current plus
  one rollback image per service in administrator-owned Namespace annotations.
- [x] Replace ConfigMap-parameterized admission rules with `namespaceObject` rules and
  verify allow/deny behavior against the real Kubernetes 1.36 API server without leaving
  temporary cluster-scoped resources.
- [x] Make deploy and rollback workflows assert the scoped RBAC contract, clean credentials
  with `always()`, and clean the readiness Job on failed smoke execution.
- [x] Preflight every rollback revision with `--dry-run=server` before all actual undo
  requests, then wait for every Deployment and retain structured failure evidence.
- [x] Update deployment contracts, runbook/spec/evidence, full tests, lint, actionlint and
  final independent security review while keeping external deployment gates blocked.
