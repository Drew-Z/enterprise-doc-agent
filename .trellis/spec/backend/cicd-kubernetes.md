# CI/CD And Kubernetes

## Adopted Facts

- API, Worker, consumer and Web have separate non-root Dockerfiles.
- Kubernetes base defines migration, startup/readiness/liveness probes, resource bounds,
  ServiceAccount, PDB and NetworkPolicy. Scoped deployer RBAC and admission policies live
  in the administrator-applied `infra/k8s/bootstrap/` bundle.
- The reviewed migration Job runs Alembic, idempotent official LangGraph checkpointer
  setup, and an explicit read-only check before any application rollout.
- Staging/prod Kustomize overlays use digest-form image identities.
- The `tiny-single-node` staging overlay is the reviewed 2-vCPU/2-GiB K3s profile. It
  selects Traefik, removes PDBs that cannot provide single-node availability, keeps the
  existing workloads plus migration Job at 992 MiB of memory limits, and uses zero-surge
  application rollouts so rollout overlap cannot exceed that budget.
- Tiny staging runs an ephemeral, bounded Redis delivery layer with `noeviction` and
  explicit ingress policy. PostgreSQL/pgvector, object storage, model providers, and
  retained observability remain external; Redis loss is recovered from PostgreSQL and
  the transactional Outbox rather than treated as durable business state.
- Measured tiny staging uses 15-second database/object-store connection budgets, a
  60-second checkpointer budget, and a Worker readiness probe that can cover that budget.
  Production defaults remain unchanged.
- Real admission verification of a changed fixed-name migration Job requires the previous
  completed Job to be backed up and deleted first. Kubernetes immutable-field validation
  otherwise fails before the new Job command can reach the admission allow/deny matrix.
- Pull-request images are built locally for contract checks. Tagged releases use one
  push build, then bind Trivy, SPDX SBOM, BuildKit provenance, Cosign signature and
  attestation verification to the returned immutable `image@sha256:digest`.
- BuildKit provenance is schema-validated before signing. Legacy SLSA v0.2 predicates
  use `slsaprovenance02`; current SLSA v1 predicates require both `buildDefinition` and
  `runDetails` and use `slsaprovenance1`. The detected type is recorded as evidence and
  reused for Cosign attest and verify so a valid predicate is never mislabeled.
- Release workflow diagnostics and step outcomes are uploaded with `always()`; the
  scan/sign/verification steps still fail the job and are not hidden by allow-failure.
- Trivy uses `trivy-action` v0.36.0 by immutable commit with scanner version v0.70.0
  explicit. Pinning only an outer composite action is insufficient when that revision
  still references mutable or deleted nested action tags.
- Web images receive `VITE_OBJECT_STORE_ORIGINS` at build time. Staging configuration
  accepts only HTTPS public/object-store endpoints and verifies that the presign origin
  is present in that Web allowlist.
- For Cloudflare R2, the account `r2.cloudflarestorage.com` S3 endpoint is used by both
  the control and presign clients unless a separately reviewed upload proxy exists. An
  R2 public custom domain is a read-oriented public bucket surface, not a replacement
  for the S3 SigV4 multipart-upload endpoint and must not be attached to a private
  documents bucket merely to satisfy a Web origin allowlist.
- Staging database egress selects only API, Worker, consumer, and migration Pods. The
  configured destination must be one public global-unicast IPv4 `/32` or IPv6 `/128`;
  broad, private, loopback, and reserved CIDRs fail before cluster apply.
- Staging and rollback remain manual environment workflows. Administrators own Namespace,
  ConfigMap, ServiceAccount, Service, Ingress, NetworkPolicy, PDB and Secret prerequisites;
  the deployer verifies those objects, then owns migration-before-workload ordering and
  redacted rollout evidence without reading or committing Secret values.
- Staging fixes the model provider to `openai_compatible`, reads the non-secret HTTPS
  `/v1` base URL and exact model name from protected GitHub Environment variables,
  and reads only the API key from `enterprise-doc-secrets`. Model route changes hash
  the complete ConfigMap into API, Worker, consumer and migration Pod templates so a
  route update cannot leave stale processes running. The non-secret route is retained
  in the release record; the API key is never evidence.
- Staging and rollback target the fixed `enterprise-doc-staging` self-hosted runner
  label. The runner is repository-scoped, runs as a dedicated non-root user, and is
  installed on the private K3s node; GitHub-hosted runners cannot reach the private
  Kubernetes API. Build and automatic quality jobs remain on GitHub-hosted runners;
  the full integration/evidence workflow is a separately dispatched GitHub-hosted job.
  `quality-self-hosted.yml` is a manual, serial fallback for account-level hosted-runner
  outages. It runs the same fast backend/frontend commands on the fixed runner without
  Kubernetes commands, deployment environments, repository secrets, or Actions caches.
  Python, uv, Node.js and pnpm are pinned and installed by
  `provision-runner-toolchain.sh`; the fallback validates those exact versions and does
  not use network-dependent setup actions.
  It is fallback evidence and does not replace the automatic GitHub-hosted Quality gate.
- Release CI aggregates all four immutable image results into one strict manifest.
  The administrator preflight validates Secret structure and TLS SAN; the scoped
  staging workflow validates the exact Kubernetes context and registry prefix, performs
  server-side dry-run, and sanitizes raw evidence before hashing it.
- `STAGING_DEPLOYMENT_PROFILE` selects an allowlisted staging profile and defaults to
  `tiny-single-node`. It is a repository variable instead of an eleventh dispatch input
  because GitHub limits `workflow_dispatch` to ten inputs. The selected profile is part
  of release evidence so a rollout cannot be described without its resource shape. The
  Namespace stores the same profile and deployment rejects existing unannotated or
  mismatched Namespaces before applying migration or workloads.
- Model base URL and name are also Environment variables rather than dispatch inputs;
  adding them as inputs would exceed the same ten-input platform limit.
- The R2 multipart checksum mode is the protected Environment variable
  `STAGING_OBJECT_STORE_CHECKSUM_MODE`, not a dispatch input. This keeps the workflow at
  ten inputs and makes the provider-specific `readback_sha256` contract part of the
  reviewed environment rather than an operator choice on every release.
- Tiny inherits the staging image transformer, so immutable image edits occur in the
  staging parent overlay before rendering either profile. CI and deploy pin Kustomize
  5.7.1; 5.6.0 panics on the tiny multi-document delete patch.
- The in-cluster readiness smoke receives a dedicated Pod label and staging-only API
  ingress policy. `deployment-profile.txt` is written to raw evidence before sanitizing,
  so its sanitized copy is included in the evidence manifest hash chain.
- The authenticated staging smoke keeps a 300-second end-to-end budget and a 90-second
  per-request transport budget. The transport budget must cover measured external database and
  ingestion latency without turning a completed request into a client timeout, while remaining
  bounded below the end-to-end gate.
- Restore and rollback commands are dry-run or validation-only without explicit
  `--confirm`.
- R2 recovery uses an application-level manifest because R2 does not implement S3 bucket
  versioning. Snapshot and restore commands validate the expected endpoint host, an
  explicit bucket allowlist, database reference size/SHA-256 and readback integrity; the
  restore command additionally requires an `enterprise_doc_restore_` database and writes
  only below `enterprise-doc-recovery/restores/<restore-id>/`.
- R2 snapshot keys live below `enterprise-doc-recovery/snapshots/<drill-id>/` and require
  matching Cloudflare Bucket Lock rules as an operator prerequisite. Manifests contain
  private object keys and reference IDs, stay out of ordinary deployment evidence, and are
  written locally with owner-only permissions. Public records contain only aggregate
  counts, endpoint host, prefixes, timing and hashes.
- R2 objects above the 4.995 GiB single-request limit use `UploadPartCopy`; multipart copy
  rechecks the source ETag after completion and every destination is streamed back through
  SHA-256 validation. The restored inventory must equal the isolated database and manifest
  reference set in both directions.
- `scripts/local_recovery_drill.py` requires `--confirm-local`, restores only to an
  `enterprise_doc_restore_` database, compares Alembic/table inventories, and still
  reports `blocked_external` without object-store restore and Kubernetes rollback.
- The independent local recovery Compose binds PostgreSQL, Redis and MinIO only to
  loopback ports and gives each service a recovery-specific named volume. Its PG17 image
  builds pgvector with the pinned server's own PGXS toolchain; an unversioned Alpine
  `postgresql-pgvector` package is invalid because it may target a newer PostgreSQL major.
- A recovery application profile starts only after the recovered dependencies are healthy.
  Recreating PostgreSQL, Redis or MinIO invalidates existing API, publisher and consumer
  processes; restart all three before readiness checks and authenticated application smoke
  so stale connection pools cannot be mistaken for a restore failure.
- `scripts/staging_smoke.py --allow-loopback-http` may relax HTTPS only for an explicit
  local recovery drill. Both control-plane and presigned object-store hosts must be in
  their exact allowlists and resolve syntactically to `localhost` or a loopback IP; the
  default staging path continues to require HTTPS and rejects loopback endpoints.
- Recovery and capacity evidence is validated by
  `scripts/validate_recovery_capacity_evidence.py`. A `passed` report requires external
  environment and cluster identity, immutable commit/image identity, timezone-aware
  timings, measured results, hashed artifacts and the evidence-type-specific checks.
  Passed recovery evidence also requires objectives approved before execution, an
  isolated source/recovery scope, a recorded failure timeline whose calculated RPO/RTO
  match the reported measurements and objectives, and an independent post-run review.
  Missing cloud, registry, cluster, restore or production-like load prerequisites must
  remain `blocked_external` with a reason and prerequisite list.
- Local image builds, static checks and local rendering evidence do not prove registry,
  cloud-cluster, TLS, secret-manager, staging smoke or rollback execution.
- The deployer retrieves the live prerequisite objects and compares their normalized
  specs and inventory with the administrator-approved render. Comparing only a stored
  Namespace fingerprint is insufficient because an administrator-side object could
  drift without updating that annotation.
- Deploy and rollback bind kubeconfig use to the protected expected context, API server
  and Namespace UID. A matching context name alone does not identify a cluster.
- Repeated rollout deletes the completed fixed-name migration Job before its server-side
  create dry-run because Kubernetes Job Pod templates are immutable; workload dry-runs
  and administrator prerequisite validation still happen before that deletion.
- Deploy and rollback write kubeconfig only to a job-unique `runner.temp` path with
  signal/error and `always()` cleanup. Abnormal runner termination still requires token
  rotation when temporary-file cleanup cannot be proven.
- A Ready single-node K3s control plane is not a passed staging rollout. Host firewall
  state, private `6443`/`10250`/`8472` reachability, system-Pod memory, swap pressure,
  application apply, authenticated smoke and rollback remain separate evidence gates.

## Proven Examples

- `evidence/m6/20260719-local-image-builds.json` records successful local builds and
  runtime-user checks for API, Worker, consumer and Web images.
- `scripts/render_k8s_phase.py` structurally separates prerequisites, migration and
  workloads from one Kustomize render before the staging workflow applies them.
- `scripts/staging_smoke.py` implements the authenticated upload-to-Agent main-path
  smoke, while a real staging run remains an external gate.
- `scripts/configure_staging_manifest.py` binds one rendered staging manifest to the
  reviewed HTTPS hosts, TLS Secret name, Web origin allowlist, single-host database
  egress CIDR, model route, prerequisite fingerprint and current/rollback image allowlists
  without writing credential data.
- `scripts/validate_buildkit_provenance.py` fail-closes on incomplete, ambiguous or
  unknown BuildKit predicate shapes and returns only Cosign's reviewed v0.2/v1 type.
- `scripts/validate_recovery_capacity_evidence.py` rejects local-only recovery or
  capacity records that claim `passed`, verifies artifact hashes and enforces RPO/RTO,
  smoke, repeated-load, percentile and dependency-telemetry contracts.
- `scripts/validate_staging_secrets.py` is an administrator-side preflight that validates
  required non-empty app keys, Docker registry JSON, TLS certificate validity/key match
  and exact or single-label wildcard SAN coverage without retaining values.
- `scripts/validate_staging_prerequisites.py` compares the rendered approval annotations
  and prerequisite fingerprint with the live administrator-owned Namespace, and compares
  normalized live prerequisite inventory/specs with the approved render.
- `scripts/verify_staging_admission.py` exercises RBAC and parameter-free admission
  allow/deny behavior against a real API server with isolated policy names and cleanup.
- `scripts/sanitize_deployment_evidence.py` structurally redacts JSON/YAML Secret data,
  credentials, DSN passwords, bearer tokens and signed query parameters.
- `scripts/run_recovery_orchestrator.py` defaults to dry-run, requires the exact
  `run-recovery-drill` confirmation, executes argv lists without a shell, records every
  preflight/recovery/cleanup phase and derives RPO/RTO from the recorded failure boundary.
- Local recovery smoke may use an explicitly allowlisted loopback HTTP control plane while
  presigned object URLs remain either allowlisted HTTPS or allowlisted loopback HTTP; local
  mode never permits arbitrary public HTTP object-store endpoints.
- `tests/deployment/` checks digest-pinned bases, non-root runtime contracts,
  migration ordering, smoke redaction, final-digest supply-chain binding, staging host
  configuration and release/evidence safeguards.
- `enterprise-doc-object-snapshot` and `enterprise-doc-object-restore` are deployable Core
  console commands for the R2 snapshot and isolated-prefix restore contract.

## Proven Files

- `infra/docker/`
- `infra/k8s/`
- `infra/k8s/bootstrap/`
- `infra/k8s/smoke/`
- `.github/workflows/container.yml`
- `.github/workflows/deploy-staging.yml`
- `.github/workflows/rollback.yml`
- `scripts/configure_staging_manifest.py`
- `scripts/validate_recovery_capacity_evidence.py`
- `scripts/local_recovery_drill.py`
- `scripts/run_recovery_orchestrator.py`
- `scripts/build_release_manifest.py`
- `scripts/validate_buildkit_provenance.py`
- `scripts/build_staging_release_record.py`
- `scripts/validate_staging_secrets.py`
- `scripts/validate_staging_prerequisites.py`
- `scripts/verify_staging_admission.py`
- `scripts/sanitize_deployment_evidence.py`
- `tests/deployment/`

## Scenario: Rebuild A 4C8G Single-Node Staging Host

### 1. Scope / Trigger

- Trigger: a clean Ubuntu 24.04 node replaces the tiny host or the selected deployment
  profile changes to `single-node-4c8g`.

### 2. Signatures

- Read-only audit: `bootstrap-host.sh --check`.
- Mutating baseline: `bootstrap-host.sh --apply --operator-ssh-cidr <CIDR>
  --confirm-console-access`.
- K3s: `install-k3s.sh --check|--apply`.
- Toolchain: `provision-runner-toolchain.sh --check|--apply`.
- Profile: `STAGING_DEPLOYMENT_PROFILE=single-node-4c8g`.

### 3. Contracts

- Host is Ubuntu 24.04 with at least four CPUs and 7.5 GiB RAM.
- K3s is exactly `v1.36.2+k3s1`; Kustomize is exactly `v5.7.1`.
- `STAGING_OBJECT_STORE_CHECKSUM_MODE=readback_sha256` for Cloudflare R2.
- Durable database/object state, model routes and retained telemetry remain external.
- Repository automation never receives registration, tunnel, database, R2 or model tokens.

### 4. Validation & Error Matrix

- Missing explicit operator CIDR -> baseline refuses mutation.
- Missing console/TAT acknowledgement -> baseline refuses mutation.
- Active K3s during baseline -> baseline refuses mutation.
- Wrong OS/capacity or artifact checksum -> relevant script exits non-zero.
- Public control-plane reachability -> keep `STAGING_CONTROL_PLANE_APPROVED` false.
- Existing Namespace profile mismatch -> deploy exits before prerequisites or workloads.

### 5. Good/Base/Bad Cases

- Good: exact host, explicit `/32` or `/128`, verified key plus console, green local tests,
  private control plane and a final `single-node-4c8g` render.
- Base: `--check` records facts without changing packages, SSH, firewall or services.
- Bad: opening K3s ports publicly, treating two replicas on one node as HA, or claiming a
  production RAG rollout while embeddings are still deterministic.

### 6. Tests Required

- `tests/deployment/test_staging_host_baseline.py` asserts CLI, safety gates, kernel,
  firewall, SSH, exact versions and absence of token-bearing runner registration.
- `test_single_node_4c8g_overlay_renders_reviewed_capacity_shape` asserts final replicas,
  no PDB, zero surge, total CPU/memory, external state and Namespace profile.
- Actionlint must accept exactly ten workflow dispatch inputs.
- Real evidence must include rebooted-host checks, Ready system Pods, external port probes,
  authenticated smoke, rollback and recovery observations.

### 7. Wrong vs Correct

Wrong: run a mutable `curl | sh`, leave SSH/6443 open to the Internet, and call the Ready
node production. Correct: verify repository-pinned artifact hashes, apply the explicit
operator allowlist with console recovery, keep cluster ports private, and record every
remaining external gate separately.
