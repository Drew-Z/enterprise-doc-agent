# Implementation Plan: Real Provider RAG Quality Evaluation

## Slice 1: Dataset Contract And Validation

- [x] Add a failing test for valid typed loading and stable dataset/corpus hashes.
- [x] Implement the minimum dataset models and loader.
- [x] Add failing tests for duplicate IDs, unknown references, path traversal, and absent
      quoted spans; implement each validation rule one at a time.

## Slice 2: Deterministic Quality Metrics

- [x] Add a failing test for answer, citation, grounding, and refusal metrics.
- [x] Implement observation, per-case score, and aggregate report contracts.
- [x] Add edge tests proving missing required citations and false non-refusals score zero.
- [x] Add a stable-anchor test whose runtime chunk UUID changes between observations.

## Slice 3: Synthetic Corpus And 40 Golden Cases

- [x] Add 8-12 synthetic enterprise documents with explicit section headings and unique
      evidence phrases.
- [x] Add the 40-case dataset with the required category distribution.
- [x] Validate distribution, references, source spans, and hashes in a repository test.

## Slice 4: Secret-Safe Staging Runner

- [x] Add a failing fake-client test for one answer and one refusal case.
- [x] Implement multipart upload, readiness wait, Agent polling, artifact verification,
      stable-anchor mapping, and sequential case execution.
- [x] Add filtering, bounded sample count, validation-only mode, and sanitized command
      provenance.
- [x] Add report-redaction tests covering token, endpoint, presigned URL, absolute path,
      raw corpus, and complete answer text.

## Slice 5: Local And Real Verification

- [x] Run focused tests after every slice.
- [x] Run Ruff format/check and mypy for changed Python packages/scripts.
- [x] Run the non-integration test suite.
- [x] Verify saved configuration by key presence only; never print values.
- [x] Run validation-only, then a 1-2 case real staging trial, then the bounded 8-12 case
      sample if the first trial is healthy.
- [x] Store a sanitized evidence report and validate its hashes/provenance.

## Slice 6: Honest Gate Update

- [x] Update M5/M7 blocking reasons and prerequisites only for evidence actually produced.
- [x] Keep gates `open` / `blocked_external` for missing full-run, repeatability,
      token/cost, representative-corpus, or human-review requirements.
- [x] Run evidence-contract tests and inspect the final Git diff for secrets.

## Final Checks

- [x] `uv run ruff format --check .`
- [x] `uv run ruff check .`
- [x] `uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src`
- [x] `uv run pytest -m "not integration"`
- [x] Confirm the two pre-existing untracked ops documents remain untouched.
