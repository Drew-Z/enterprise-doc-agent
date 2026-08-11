# GitHub Actions minute optimization implementation

## Checklist

- [x] Keep automatic backend/frontend Quality and ignore docs/Trellis-only events.
- [x] Move M1, M4, and Web E2E jobs unchanged into a manual heavy-quality workflow.
- [x] Add reviewed PR path filters to the container supply-chain workflow.
- [x] Update CI foundation contracts for split workflows and triggers.
- [x] Update backend quality, multipart, and CI/CD specifications.
- [x] Run focused and repository quality validation without live provider/staging operations.
- [ ] Commit, push the isolated branch, and open a PR.

## Validation Commands

```bash
uv run pytest tests/foundation/test_ci_contract.py
uv run pytest tests/deployment/test_m6_contracts.py
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
uv run pytest -m "not integration"
pnpm lint
pnpm typecheck
pnpm test
pnpm build
python ./.trellis/scripts/task.py validate ./.trellis/tasks/08-10-08-10-github-actions-minute-optimization
git diff --check
```

## Risk And Rollback Points

- Preserve heavy job commands and evidence semantics exactly while moving YAML ownership.
- Do not classify an image-affecting path as container-irrelevant.
- Do not run manual/staging workflows or expose repository secrets during validation.
- Revert the workflow split if release owners require heavy jobs on every PR again.
