# Commercial release evidence

## 1. Scope / Trigger

Use for commercial release claims, evidence aggregation and the manual Commercial
Readiness workflow. This adds a review gate, not deployment authorization or a
replacement for existing quality/recovery/capacity validators.

## 2. Signatures

`python -B scripts/check_commercial_readiness.py --manifest <relative-json> --commit <40-hex> --environment staging|production --profile single-node-4c4g --scope invited|public_saas`

`check(root: Path, manifest: str, commit: str, environment: str, profile: str, scope: str, now: datetime | None = None) -> dict`

## 3. Contracts

The fixed REQUIRED_CHECKS owns required gate IDs/checks. Public SaaS additionally
requires payments/self_service. Manifest schema_version=1, release_scope and candidate
must match the requested target. Duplicate or unknown gate IDs are rejected.

Passed gate evidence binds its JSON file SHA256, gate_id, status, target, completion
time, executor, independent review and required true checks. Original source JSON
reports have hashes, passed status, matching source commit/environment/profile and
completion time. All execution reports must be <=7 days old and not in the future.
Paths are repository-relative, contained after resolve, no drive/ADS/UNC/backslash/
parent traversal. JSON duplicate keys and reports >16 MiB are rejected.

Recovery/capacity measurement_report additionally reuses validate_evidence. Capacity
must be application profile with upload/ingestion/retrieval/generation_recovery and
preapproved acceptance_objectives: max_p95_ms, max_error_rate, min_headroom_percent,
approved_by, approved_at <= started_at. Its measured values must meet these objectives.
Existing independent recovery, RPO/RTO calculations and artifact hashes remain required.

Output contains status=blocked or eligible_for_release_review, candidate, checked_at,
validated_gates, stable issue codes and deployment_authorized=false. Exit 1 means
blocked; 0 means mechanically complete for review. Hashes and reviewer labels are
not signatures or verified identity. Human review must verify semantic truth,
representative workload, source authenticity, runtime/model configuration and consent.

The manual workflow runs with contents:read, pinned actions, no secrets/model calls/
deployment. Inputs travel through quoted environment variables. It always preserves
only this run's exact report; validation failure is never continue-on-error.

## 4. Validation & Error Matrix

| Condition | Code / result |
|---|---|
| Missing/invalid manifest | evidence_file_unavailable / invalid_json, blocked |
| Path escapes root | unsafe_evidence_path |
| Missing/duplicate gate | required_gate_missing / duplicate_gate |
| Gate/evidence not passed | gate_not_passed / evidence_not_passed |
| Hash or candidate mismatch | evidence_digest_mismatch / candidate_binding_mismatch |
| Old/future execution or review | evidence_not_current / invalid_review_time |
| Missing/self review | review_not_approved / independent_review_required |
| Missing source/failed source | source_reports_missing / source_report_not_passed |
| Wrong raw source scope | source_commit_mismatch / source_environment_mismatch |
| Readiness-only capacity | business_capacity_required |
| Recovery/capacity native contract fails | measurement_contract_failed |
| Capacity exceeds preapproved target | capacity_objective_failed |

## 5. Good / Base / Bad Cases

Good: exact candidate reviewed evidence supports a release review. Base: the checked-in
current manifest remains blocked and reports all missing gates. Bad: copy a historical
passed envelope, relabel its SHA or use ready-only load to claim business capacity.

## 6. Tests Required

tests/deployment/test_commercial_readiness.py invokes the CLI for missing/path failures
and the public check interface for complete invited/public fixtures, scope narrowing,
missing/duplicate/unknown gates, source drift, tampering, review/time boundaries,
readiness-only capacity, preapproved limit breaches and non-independent recovery.
Synthetic fixtures are never published as real release evidence. Retain native
test_validate_recovery_capacity_evidence.py regressions.

## 7. Wrong vs Correct

Wrong: status=passed alone means production-ready, or auto-approve a release after CLI
exit 0. Correct: validate evidence bindings and required checks, preserve failures,
then perform real independent review and existing deployment authorization.

## Proven Examples

- `scripts/check_commercial_readiness.py`
- `tests/deployment/test_commercial_readiness.py`
- `.github/workflows/commercial-readiness.yml`
- `evidence/gates/commercial-readiness-current.json` (blocked, not a passing template)
