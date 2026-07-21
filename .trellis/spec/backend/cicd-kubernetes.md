# CI/CD And Kubernetes

## Adopted Facts

- API, Worker, consumer and Web have separate non-root Dockerfiles.
- Kubernetes base defines migration, startup/readiness/liveness probes, resource bounds,
  ServiceAccount/RBAC, PDB and NetworkPolicy.
- Staging/prod Kustomize overlays use digest-form image identities.
- The `tiny-single-node` staging overlay is the reviewed 2-vCPU/2-GiB K3s profile. It
  selects Traefik, removes PDBs that cannot provide single-node availability, keeps the
  existing workloads plus migration Job at 928 MiB of memory limits, and uses zero-surge
  application rollouts so rollout overlap cannot exceed that budget.
- Tiny staging runs an ephemeral, bounded Redis delivery layer with `noeviction` and
  explicit ingress policy. PostgreSQL/pgvector, object storage, model providers, and
  retained observability remain external; Redis loss is recovered from PostgreSQL and
  the transactional Outbox rather than treated as durable business state.
- Pull-request images are built locally for contract checks. Tagged releases use one
  push build, then bind Trivy, SPDX SBOM, BuildKit provenance, Cosign signature and
  attestation verification to the returned immutable `image@sha256:digest`.
- Release workflow diagnostics and step outcomes are uploaded with `always()`; the
  scan/sign/verification steps still fail the job and are not hidden by allow-failure.
- Trivy uses `trivy-action` v0.36.0 by immutable commit with scanner version v0.70.0
  explicit. Pinning only an outer composite action is insufficient when that revision
  still references mutable or deleted nested action tags.
- Web images receive `VITE_OBJECT_STORE_ORIGINS` at build time. Staging configuration
  accepts only HTTPS public/object-store endpoints and verifies that the presign origin
  is present in that Web allowlist.
- Staging database egress selects only API, Worker, consumer, and migration Pods. The
  configured destination must be one public global-unicast IPv4 `/32` or IPv6 `/128`;
  broad, private, loopback, and reserved CIDRs fail before cluster apply.
- Staging and rollback remain manual environment workflows. Staging owns ingress/TLS,
  private-registry Secret names, migration-before-workload ordering and redacted
  rollout evidence, but never commits Secret values.
- Release CI aggregates all four immutable image results into one strict manifest;
  staging validates the exact Kubernetes context, registry prefix, Secret structure and
  TLS SAN, performs server-side dry-run, and sanitizes raw evidence before hashing it.
- `STAGING_DEPLOYMENT_PROFILE` selects an allowlisted staging profile and defaults to
  `tiny-single-node`. It is a repository variable instead of an eleventh dispatch input
  because GitHub limits `workflow_dispatch` to ten inputs. The selected profile is part
  of release evidence so a rollout cannot be described without its resource shape. The
  Namespace stores the same profile and deployment rejects existing unannotated or
  mismatched Namespaces before applying prerequisites.
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

## Proven Examples

- `evidence/m6/20260719-local-image-builds.json` records successful local builds and
  runtime-user checks for API, Worker, consumer and Web images.
- `scripts/render_k8s_phase.py` structurally separates prerequisites, migration and
  workloads from one Kustomize render before the staging workflow applies them.
- `scripts/staging_smoke.py` implements the authenticated upload-to-Agent main-path
  smoke, while a real staging run remains an external gate.
- `scripts/configure_staging_manifest.py` binds one rendered staging manifest to the
  reviewed HTTPS hosts, TLS Secret name, Web origin allowlist, and single-host database
  egress CIDR without writing credential data.
- `scripts/validate_recovery_capacity_evidence.py` rejects local-only recovery or
  capacity records that claim `passed`, verifies artifact hashes and enforces RPO/RTO,
  smoke, repeated-load, percentile and dependency-telemetry contracts.
- `scripts/validate_staging_secrets.py` validates required non-empty app keys, Docker
  registry JSON, TLS certificate validity/key match and exact or single-label wildcard
  SAN coverage without retaining values.
- `scripts/sanitize_deployment_evidence.py` structurally redacts JSON/YAML Secret data,
  credentials, DSN passwords, bearer tokens and signed query parameters.
- `tests/deployment/` checks digest-pinned bases, non-root runtime contracts,
  migration ordering, smoke redaction, final-digest supply-chain binding, staging host
  configuration and release/evidence safeguards.

## Proven Files

- `infra/docker/`
- `infra/k8s/`
- `.github/workflows/container.yml`
- `.github/workflows/deploy-staging.yml`
- `.github/workflows/rollback.yml`
- `scripts/configure_staging_manifest.py`
- `scripts/validate_recovery_capacity_evidence.py`
- `scripts/local_recovery_drill.py`
- `scripts/build_release_manifest.py`
- `scripts/build_staging_release_record.py`
- `scripts/validate_staging_secrets.py`
- `scripts/sanitize_deployment_evidence.py`
- `tests/deployment/`
