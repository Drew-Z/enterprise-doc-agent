# CI/CD And Kubernetes

## Adopted Facts

- API, Worker, consumer and Web have separate non-root Dockerfiles.
- Kubernetes base defines migration, startup/readiness/liveness probes, resource bounds,
  ServiceAccount/RBAC, PDB and NetworkPolicy.
- Staging/prod Kustomize overlays use digest-form image identities.
- Container workflow defines build, Trivy scan, SBOM and release push; staging and
  rollback are manual environment workflows.
- Restore and rollback commands are dry-run or validation-only without explicit
  `--confirm`.
- Local image builds, static checks and local rendering evidence do not prove registry,
  cloud-cluster, TLS, secret-manager, staging smoke or rollback execution.

## Proven Examples

- `evidence/m6/20260719-local-image-builds.json` records successful local builds and
  runtime-user checks for API, Worker, consumer and Web images.
- `scripts/render_k8s_phase.py` structurally separates prerequisites, migration and
  workloads from one Kustomize render before the staging workflow applies them.
- `scripts/staging_smoke.py` implements the authenticated upload-to-Agent main-path
  smoke, while a real staging run remains an external gate.
- `tests/deployment/` checks digest-pinned bases, non-root runtime contracts,
  migration ordering, smoke redaction and release-script safeguards.

## Proven Files

- `infra/docker/`
- `infra/k8s/`
- `.github/workflows/container.yml`
- `.github/workflows/deploy-staging.yml`
- `.github/workflows/rollback.yml`
- `tests/deployment/`
