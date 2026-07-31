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

## Slice 10: Real Tiny-Staging Stabilization

- [ ] Make the reviewed migration Job run Alembic followed by idempotent LangGraph
  checkpointer setup and verification before any application rollout.
- [ ] Give the tiny single-node profile bounded external-dependency and startup probe
  budgets that match measured Supabase/R2 latency without changing production defaults.
- [ ] Keep the tiny overlap budget at or below 1 GiB while allowing the Worker enough
  CPU, memory and startup time to become ready on the 2-vCPU/2-GiB K3s node.
- [ ] Emit immediate non-secret migration Job status and describe diagnostics when the
  pre-rollout wait fails, then verify the change against the real staging cluster.

## Slice 11: Rebuilt 4C8G Staging Host And Profile

- [x] Add red contract tests for the host baseline CLI, explicit SSH CIDR gate, SSH/UFW
  safety, swap/modules/sysctls/journald configuration, exact K3s version and root-owned
  deployment toolchain.
- [x] Add red render/workflow tests for `single-node-4c8g`, including exact replicas,
  no PDB, zero-surge rollout, external durable dependencies, resource ceilings and
  Namespace profile ownership.
- [x] Implement idempotent Ubuntu 24.04 check/apply automation and a separately pinned
  K3s install/verify path; keep runner/tunnel/app secrets out of repository automation.
- [x] Implement the 4C8G Kustomize overlay and add it to the strict deploy allowlist while
  continuing to bind image digests through the staging parent overlay.
- [x] Run deployment tests, Kustomize renders, shell syntax/static checks, backend lint,
  Actionlint and Trellis validation before any server mutation.
- [ ] Apply the baseline to the rebuilt Tencent host, reboot, re-verify SSH/TAT/firewall,
  install K3s and capture a sanitized host/cluster observation.
- [ ] Apply bootstrap RBAC/admission, provision admin-owned prerequisites and Secrets,
  register the repository-scoped runner, configure Cloudflare Tunnel, publish immutable
  images, dispatch staging, and collect smoke/rollback/recovery evidence.

## Slice 12: Runtime Blockers Before Real Rollout

- [x] Reproduce and fix production-path SQLAlchemy model registration so an isolated
  ingestion consumer cannot raise `NoReferencedTableError` during flush.
- [x] Add bounded publisher-cycle timeout and task supervision so a hung or failed Outbox
  publisher cannot leave a healthy-looking Worker process.
- [x] Add explicit database pool capacity/wait/recycle settings and contract tests against
  the total 4C8G replica connection budget.
- [x] Collect current and previous Worker/consumer logs only into the raw evidence area,
  pass them through the existing sanitizer, and upload only sanitized output.
