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
  Kubernetes API. Build and quality jobs remain on GitHub-hosted runners.
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
- Tiny inherits the staging image transformer, so immutable image edits occur in the
  staging parent overlay before rendering either profile. CI and deploy pin Kustomize
  5.7.1; 5.6.0 panics on the tiny multi-document delete patch.
- The in-cluster readiness smoke receives a dedicated Pod label and staging-only API
  ingress policy. `deployment-profile.txt` is written to raw evidence before sanitizing,
  so its sanitized copy is included in the evidence manifest hash chain.
- Restore and rollback commands are dry-run or validation-only without explicit
  `--confirm`.
- `scripts/local_recovery_drill.py` requires `--confirm-local`, restores only to an
  `enterprise_doc_restore_` database, compares Alembic/table inventories, and still
  reports `blocked_external` without object-store restore and Kubernetes rollback.
- Recovery and capacity evidence is validated by
  `scripts/validate_recovery_capacity_evidence.py`. A `passed` report requires external
  environment and cluster identity, immutable commit/image identity, timezone-aware
  timings, measured results, hashed artifacts and the evidence-type-specific checks.
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
- `tests/deployment/` checks digest-pinned bases, non-root runtime contracts,
  migration ordering, smoke redaction, final-digest supply-chain binding, staging host
  configuration and release/evidence safeguards.

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
- `scripts/build_release_manifest.py`
- `scripts/validate_buildkit_provenance.py`
- `scripts/build_staging_release_record.py`
- `scripts/validate_staging_secrets.py`
- `scripts/validate_staging_prerequisites.py`
- `scripts/verify_staging_admission.py`
- `scripts/sanitize_deployment_evidence.py`
- `tests/deployment/`
