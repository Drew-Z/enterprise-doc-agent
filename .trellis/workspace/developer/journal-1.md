# Journal - developer (Part 1)

> AI development session journal
> Started: 2026-07-17

---



## Session 1: Close external delivery gates

**Date**: 2026-07-20
**Task**: Close external delivery gates
**Package**: core
**Branch**: `feat/m4-agent-mcp-hitl`

### Summary

Implemented digest-bound container delivery gates with Trivy, SPDX SBOM, BuildKit provenance, Cosign signing and verification evidence; added HTTPS staging deployment configuration and deterministic recovery/capacity evidence validation. Local verification passed: 611 backend tests, pnpm quality, actionlint, base/staging/prod Kustomize rendering, and four non-root image builds. Registry/GitHub Actions, staging cluster, isolated recovery, and production-like application/GPU capacity gates remain explicitly blocked_external because no remote, cluster, credentials, restore target, or production-like environment was available.

### Main Changes

- Hardened the container release workflow so push, Trivy scanning, SPDX SBOM,
  BuildKit provenance, Cosign signing, verification, and failure diagnostics are
  tied to the immutable published digest.
- Added HTTPS staging configuration for ingress, TLS, private registry pulls,
  exact object-store origins, migration-first rollout, and sanitized evidence
  collection.
- Added deterministic validators for staging manifest inputs and for recovery,
  application-capacity, and model-capacity evidence. Missing external resources
  must be recorded as `blocked_external`, never as a passing result.
- Updated the deployment contract, README, Trellis task records, research
  sources, and the hashed external-gate status artifact.

### Git Commits

| Hash | Message |
|------|---------|
| `c4160d5bf6f5249c0b93ea2e10cefac0acae845f` | (see git log) |
| `8bf0ab52d3778c3a163ebba2938363d82149c50c` | (see git log) |

### Testing

- [OK] `uv run pytest -q`: 611 passed.
- [OK] `pnpm quality`: backend quality checks, 138 frontend tests, linting,
  type checking, and the frontend production build passed.
- [OK] `rhysd/actionlint:1.7.7` passed for all GitHub Actions workflows.
- [OK] `kubectl kustomize` rendered base, staging, and production overlays.
- [OK] API, worker, consumer, and web images built and ran as non-root users;
  the web bundle contained the configured HTTPS object-store origin.

### Status

[OK] **Completed**

### Next Steps

- Execute the registry/GitHub Actions, staging rollout, isolated recovery, and
  production-like application/GPU capacity gates when the required external
  repository, cluster, credentials, restore target, and test environment exist.
