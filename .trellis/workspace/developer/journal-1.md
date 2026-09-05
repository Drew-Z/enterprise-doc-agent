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


## Session 2: Bound 4C4G staging Worker cold-pull rollout

**Date**: 2026-09-02
**Task**: Bound 4C4G staging Worker cold-pull rollout
**Package**: core
**Branch**: `agent/grok-provider-rollout-evidence`

### Summary

Added a profile-specific 1800s Worker rollout deadline and bounded workflow waits, documented the verified OCI relay fallback, passed 993 non-integration tests plus deployment/lint/type/actionlint checks, and completed staging run 33570932597 with embedding, readiness, authenticated smoke, public health, and bilingual route validation.

### Main Changes

- Added a profile-specific Worker rollout deadline and bounded workflow waits for the reviewed 4C4G staging host.
- Documented the OCI relay fallback and the staging evidence collection path for migration, readiness, smoke, and provider routing.
- Kept cloud credentials, cluster operations, and production-capacity claims behind explicit external gates.

### Git Commits

| Hash | Message |
|------|---------|
| `0505513` | (see git log) |
| `db6401d` | (see git log) |

### Testing

- [OK] 993 non-integration tests passed with deployment, lint, type, and Actionlint checks.
- [OK] Staging run `33570932597` passed embedding, readiness, authenticated smoke, public health, and bilingual route validation.
- [OK] Evidence was sanitized and retained under the staging delivery record; no production-capacity claim was made.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: Public-reference-inspired RAG evaluation suite

**Date**: 2026-09-05
**Task**: Public-reference-inspired RAG evaluation suite
**Package**: core
**Branch**: `main`

### Summary

Added and validated a fully synthetic public-reference-inspired RAG suite with 4 documents, 17 anchors, 20 cases, provenance boundaries, repository contracts, and unchanged v2 hashes.

### Main Changes

- Added four fully synthetic Northstar Ledger reference-inspired documents with 17 pinned anchors.
- Added 20 fixed RAG cases covering fact, hard-negative, refusal, citation, and safety behavior, plus provenance and governance boundaries.
- Added repository contract tests and documented that the suite is evaluation-only; no runtime, provider, staging, M5, or M7 behavior was changed.

### Git Commits

| Hash | Message |
|------|---------|
| `a2977a1` | test: add public-reference RAG evaluation suite |
| `eecbc47` | chore: normalize RAG evaluation artifacts |

### Testing

- [OK] Focused repository contract tests: 15 passed.
- [OK] Full non-integration suite: 1006 passed, 125 deselected.
- [OK] Ruff format/lint, mypy, and Trellis task validation passed.
- [OK] Public-reference suite validate-only: 20 cases; v2 regression: 40 cases; no provider or staging execution.

### Status

[OK] **Completed**

### Next Steps

- None - task complete
