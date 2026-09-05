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


## Session 4: Protected staging RAG quality execution

**Date**: 2026-09-05
**Task**: Protected staging RAG quality execution
**Package**: core
**Branch**: `main`

### Summary

Added a manual GitHub Actions workflow for authenticated staging RAG quality evaluation
against the reviewed v2 dataset. The workflow supports a bounded `trial` run and a
full 40-case run, serializes with staging deployment and rollback, and keeps the
Environment token scoped to the evaluation step.

### Main Changes

- Added the `Evaluate Staging RAG Quality` workflow on the reviewed self-hosted staging
  runner with protected `staging` Environment and the shared staging concurrency lock.
- Kept Kubernetes credentials out of the workflow; only the sealed evaluator report is
  uploaded as an artifact.
- Added CI contract coverage and documented that a workflow pass does not close M5/M7
  without stable provider revision, cost metadata, representative corpus review, and
  independent human semantic approval.

### Testing

- [OK] Focused CI and evaluator tests: 16 passed.
- [OK] Full non-integration suite: 1007 passed, 125 deselected.
- [OK] Ruff format/lint, `git diff --check`, and YAML structural validation passed.
- [NOT RUN] Actionlint is not installed in the local environment; no authenticated
  staging evaluation was dispatched.

### Status

[OK] **Implemented; external quality gate remains open**

### Next Steps

- Configure the protected staging Environment and dedicated runner, then dispatch
  `trial` before `full` once provider revision, billing metadata, corpus approval, and
  independent reviewer inputs are available.


## Session 5: Staging RAG operational handoff and report isolation

**Date**: 2026-09-05
**Task**: M5 protected staging quality execution
**Package**: infrastructure, foundation-tests
**Branch**: `main`

### Summary

Bound uploaded artifacts to the exact run/attempt report, preventing stale runner files
from entering a later result. Added the 4C4G evaluation handoff and verified the actual
v2 trial selection is 12 cases, correcting the earlier documentation's count of ten.

### Main Changes

- Added a regression contract for exact artifact selection and missing-report failure.
- Documented Environment prerequisites, PowerShell dispatch/download, report integrity
  and evaluator SHA verification, failure handling, retained test data, and billing gaps.
- Recorded the artifact contract in the Trellis spec and preserved the open M5/M7 gates.

### Git Commits

| Hash | Message |
|------|---------|
| `ed4fef4` | fix: isolate staging RAG reports and document operation |

### Testing

- [OK] Red-to-green artifact isolation test; focused CI/evaluator suite: 17 passed.
- [OK] Full non-integration suite: 1008 passed, 125 deselected; documentation checks: 9 passed.
- [OK] Ruff format/lint, mypy, Trellis context validation, and `git diff --check` passed.
- [OK] Existing `rhysd/actionlint:1.7.7` image passed offline with a read-only repository mount.
- [OK] v2 validate-only: 12 trial and 40 full cases, unchanged dataset/corpus hashes.
- [OK] Four PowerShell examples parsed; verifier accepted valid payload and rejected wrong SHA.
- [OK] The task-created temporary validation report was removed; pre-existing files were retained.

### Status

Operational handoff implemented. No remote push, staging dispatch, provider calls or
server changes occurred. M5 remains in progress pending external quality evidence.


## Session 6: Live staging publication prerequisite audit

**Date**: 2026-09-05
**Task**: M5 protected staging quality execution
**Package**: infrastructure
**Branch**: `main`

### Summary

Read-only GitHub checks confirmed the public repository has no staging Environment
protection rules or deployment ref policy. The 4C4G runner is online and both required
host variables exist among 23 paginated variables. The smoke secret exists, but metadata
does not verify its expiry. The evaluation workflow is still local-only.

### Main Changes

- Corrected the runbook's claim that the live Environment was already protected.
- Recorded the observed remote SHA, runner/variable presence, token metadata limits,
  and proposed repository permission/ref restrictions and publication sequence.
- Kept independent reviewer ownership, token validity and real provider quality open.

### Git Commits

| Hash | Message |
|------|---------|
| `5c36592` | docs: record live staging publication prerequisites |

### Testing and Status

- [OK] Read-only GitHub API checks and remote-main ancestry validation.
- [OK] Nine documentation/repository tests, Trellis validation and `git diff --check`.
- No remote settings, secrets, refs or server workloads changed; no live evaluation ran.
- No temporary files were created; existing files and registered worktrees were preserved.
- Await explicit push authorization before executing the documented publication scope.
