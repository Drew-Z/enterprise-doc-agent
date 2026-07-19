# CI/CD And Kubernetes

## Adopted Facts

- API, Worker, consumer and Web have separate non-root Dockerfiles.
- Kubernetes base defines migration, startup/readiness/liveness probes, resource bounds,
  ServiceAccount/RBAC, PDB and NetworkPolicy.
- Staging/prod Kustomize overlays use digest-form image identities.
- Pull-request images are built locally for contract checks. Tagged releases use one
  push build, then bind Trivy, SPDX SBOM, BuildKit provenance, Cosign signature and
  attestation verification to the returned immutable `image@sha256:digest`.
- Release workflow diagnostics and step outcomes are uploaded with `always()`; the
  scan/sign/verification steps still fail the job and are not hidden by allow-failure.
- Web images receive `VITE_OBJECT_STORE_ORIGINS` at build time. Staging configuration
  accepts only HTTPS public/object-store endpoints and verifies that the presign origin
  is present in that Web allowlist.
- Staging and rollback remain manual environment workflows. Staging owns ingress/TLS,
  private-registry Secret names, migration-before-workload ordering and redacted
  rollout evidence, but never commits Secret values.
- Restore and rollback commands are dry-run or validation-only without explicit
  `--confirm`.
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
  reviewed HTTPS hosts and TLS Secret name without writing credential data.
- `scripts/validate_recovery_capacity_evidence.py` rejects local-only recovery or
  capacity records that claim `passed`, verifies artifact hashes and enforces RPO/RTO,
  smoke, repeated-load, percentile and dependency-telemetry contracts.
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
- `tests/deployment/`
