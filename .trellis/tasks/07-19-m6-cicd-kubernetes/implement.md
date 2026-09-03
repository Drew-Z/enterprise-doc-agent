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

- [x] Make the reviewed migration Job run Alembic followed by idempotent LangGraph
  checkpointer setup and verification before any application rollout.
- [x] Give the tiny single-node profile bounded external-dependency and startup probe
  budgets that match measured Supabase/R2 latency without changing production defaults.
- [x] Keep the tiny overlap budget at or below 1 GiB while allowing the Worker enough
  CPU, memory and startup time to become ready on the 2-vCPU/2-GiB K3s node.
- [x] Emit immediate non-secret migration Job status, poll terminal conditions, and print
  describe/event diagnostics as soon as the pre-rollout migration fails.
- [x] Verify the migration diagnostics and resource envelope against the real 2C2G tiny
  staging profile; the bounded migration/embedding/readiness path was observed, but the
  complete workflow made the K3s control plane unstable, so tiny is not suitable for full
  staging. Evidence: `evidence/m6/20260820-v0.1.32-tiny-resource-envelope.json`.

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
- [x] Apply the baseline to the rebuilt Tencent host, reboot, re-verify SSH/TAT/firewall,
  install K3s and capture a sanitized host/cluster observation.
- [x] Apply bootstrap RBAC/admission, provision admin-owned prerequisites and Secrets,
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

## Slice 13: R2 Immutable Snapshot And Isolated Restore

- [x] Add a deployable Core CLI that enumerates document and Agent artifact references,
  validates source size/SHA-256, and creates an immutable R2 snapshot manifest only after
  confirmed copies pass readback validation.
- [x] Add a dry-run-first restore CLI that validates endpoint, bucket, manifest digest and
  isolated database identity before copying to a dedicated restore prefix.
- [x] Cover missing objects, checksum mismatches, target conflicts, partial-copy recovery,
  idempotent reruns, secret redaction and DB/manifest/restored-inventory equality.
- [x] Run local quality, publish an immutable image, execute the staging R2 drill against
  the retained restore database, and record cross-system evidence without closing the
  production RPO/RTO gate prematurely.

## Slice 14: Reviewed Production-Like RPO/RTO Contract

- [x] Require passed recovery evidence to bind pre-approved objectives to a deterministic
  failure/recovery timeline and reject measurements that are hand-entered or miss targets.
- [x] Require a separately named, fault-domain-isolated recovery scope, no live mutation,
  and an independent post-run reviewer before accepting `production_like` evidence.
- [x] Publish a validator-backed blocked preflight with proposed RPO/RTO values and exact
  external prerequisites without promoting the current single-node smoke.
- [ ] Obtain service-owner objective approval, provision the separate recovery target,
  execute the drill, retain sanitized hashed artifacts, and close the gate only if the
  measured values meet the pre-approved objectives.

## Slice 15: Reviewed Staging Fallback Route

- [x] Render an optional OpenAI-compatible fallback base URL and model name into the
  non-secret ConfigMap only when both protected values are present.
- [x] Include the fallback route in the backend config hash and administrator-owned
  Namespace approvals while keeping both API keys exclusively in the Secret.
- [x] Source fallback routing from protected staging Environment variables without adding
  workflow-dispatch inputs, then cover configured, absent, and partial-route failures.

## Slice 16: Versioned Embedding Rollout

- [x] Parameterize the embedding generation version from a protected staging Environment
  variable while retaining version `2` as the compatibility default.
- [x] Bind the same version through manifest approvals, the embedding rollout validator, and
  the sanitized release record so a model change cannot reuse an older generation silently.
- [x] Revalidate the reviewed `Qwen/Qwen3-Embedding-4B` route with two independent local
  40-case runs after transient provider connectivity was observed; retain the existing
  `ai.hybgzs.com` / version `2` staging identity.
- [x] Record the release decision to defer the Free `qwen3-embedding-8b` challenger. No
  version `3` protected-variable change, staging reindex, or model switch was performed.
- [ ] Validate the Free `qwen3-embedding-8b` candidate through a versioned reindex and
  repeated staging RAG quality gate before any future replacement of the reviewed 4B
  identity. This remains intentionally deferred for the current release.

## Slice 17: Canonical OCI Relay Receiver

- [x] Add a dry-run-first receiver that verifies the relay archive SHA-256 and parses OCI
  index/manifest descriptors without extracting the archive.
- [x] Normalize the import base to `docker.io/library/<relay-id>`, import all platforms with
  digest records, and fail unless every descriptor has a resolvable canonical alias.
- [x] Bind the receiver script and canonical base into the relay receipt, document the host
  operation, and regression-test missing-alias failure instead of relying on manual tags.

## Release acceptance refresh: merged v0.1.33 (2026-09-04)

- [x] Reconcile the merged `v0.1.33` / commit
  `9e9efb52a27a7a7ccf963e68d97c95722cbb72f5` staging run `33777258980` against the
  `single-node-4c4g` profile and exact API, Worker, consumer and Web image digests.
- [x] Record successful migration, workload rollout, version 3 embedding probe/reindex,
  readiness, authenticated business smoke, document ACL governance, audit
  retention/legal-hold governance and identity-binding lifecycle evidence.
- [x] Record the three strict failed attempts and the short-lived smoke-token rotation
  without persisting or printing credentials; retain the earlier `rc.2` record as history.
- [x] Publish the merged-release acceptance record at
  `evidence/m6/20260904-v0.1.33-staging-governance.json` and update the evidence index,
  README and 4C4G runbook.
- [ ] Keep M6-R5 open until a separately provisioned fault domain, approved objectives,
  measured recovery timeline and independent post-run review exist.
