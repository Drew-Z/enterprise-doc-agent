# Staging smoke failure evidence: implementation

## Preconditions

- Parent queue: `07-17-enterprise-document-agent-platform/implement.md`, cycle 1.
- User explicitly requested continuous child planning and implementation; no additional local implementation approval is needed.
- Read parent/M6 design, current smoke script/tests, caller references, deployment collector, quality/error/security contracts.
- Capture baseline of all 86 existing dirty files and the real Git index; preserve those bytes.

## Ordered work

- [x] Create/converge PRD, design and context manifests.
- [x] Red: add one CLI Agent-failure regression for JSON, exit and redaction.
- [x] Green: track observed progress, attach failure report and persist it at the CLI boundary.
- [x] Red/green: cover early failure, timeouts, malformed responses and partial artifact progress.
- [x] Red/green: configuration and output-write failures; preserve all successful-path checks.
- [x] Validate related callers and release gate, then full backend quality gates.
- [x] Record actual commands/results/source hashes in `validation.json`; update adopted spec.
- [x] Mark local validation passed and task `review`, then update parent and plan next child.

## Validation commands

From the canonical repository in PowerShell, use `.\.venv\Scripts\python.exe -X utf8 -B` for pytest with `-p no:cacheprovider` and a self-cleaning temporary `--basetemp`:

- Focused: `-m pytest tests/deployment/test_staging_smoke.py -q --tb=short`.
- Related: `-m pytest tests/deployment/test_staging_governance_smoke.py tests/deployment/test_build_staging_release_record.py tests/evaluation/test_staging_rag_quality.py tests/foundation/test_evidence_contract.py -q --tb=short`.
- Required: `-m pytest -m "not integration" -q --tb=short`.
- `.\.venv\Scripts\ruff.exe format --check .` and `.\.venv\Scripts\ruff.exe check .`.
- `.\.venv\Scripts\mypy.exe packages/core/src apps/api/src apps/worker/src apps/mcp/src` (cache redirected to task temporary storage).
- `git diff --check` and `.trellis/scripts/task.py validate 09-09-staging-smoke-failure-evidence`.

No test may contact staging or a real model provider. Final results replace planned commands in the validation record with the exact invoked argv and environment.

## Scope and completion boundary

Code: `scripts/staging_smoke.py`, `tests/deployment/test_staging_smoke.py`. Documentation: adopted CI/CD spec, this child, parent queue/assessment and M6 child link. Broaden only if a regression proves a necessary caller change. Local validation does not authorize commit/push, deployment or retrospective release acceptance; keep task in review until the commit gate is satisfied.
