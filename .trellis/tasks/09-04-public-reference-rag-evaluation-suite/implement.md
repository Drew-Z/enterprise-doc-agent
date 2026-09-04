# Implementation Plan: Public-reference-inspired RAG evaluation suite

## Ordered TDD checklist

- [x] Add one failing repository contract test for loading the absent
      rag_quality_public_reference_v1.json, its independent version, four document keys, and
      exact 20-case distribution. Test load_rag_quality_dataset with no mocks.
- [x] Create four UTF-8 fictional corpus documents with original Northstar Ledger content and
      the 17 planned anchor sentences.
- [x] Create the dataset with fixed case IDs, references, accepted/forbidden variants,
      evidence-only refusal codes, and explicit limitations. Re-run the first test to green.
- [x] Add a red/green contract test for answer grounding, refusal emptiness, and explicit
      forbidden behavior in each named safety case. Inspect schema objects rather than
      duplicating validation logic.
- [x] Add a preservation assertion for v2's pre-task dataset/corpus hashes.
- [x] Add the repository provenance document beside the new dataset, including source mapping,
      the OWASP no-copy boundary, mandatory human review, and M5/M7 non-closure.
- [x] Run focused tests and validate-only for both datasets; inspect reports for the explicit
      no-staging/provider limitation.
- [x] Run Ruff, Trellis validation, and git diff check; review for source copying, baseline
      edits, leakage, and overclaims. Capture the reusable synthetic-suite boundary in the M5
      evaluation specification.
- [x] Update task checkboxes truthfully and complete an in-session implementation review. Do not
      deploy or run a provider.

## Validation commands

    Set-Location 'D:\workspace4Cursor\enterprise-doc-agent'
    uv run pytest packages/core/tests/test_rag_quality_evaluation.py -q
    uv run python scripts/evaluate_staging_rag_quality.py --dataset evaluation/rag_quality_public_reference_v1.json --validate-only --report-path "$env:TEMP\rag-quality-public-reference-v1-validate.json"
    uv run python scripts/evaluate_staging_rag_quality.py --dataset evaluation/rag_quality_v2.json --validate-only --report-path "$env:TEMP\rag-quality-v2-regression-validate.json"
    uv run ruff check packages/core/tests/test_rag_quality_evaluation.py
    uv run python ./.trellis/scripts/task.py validate 09-04-public-reference-rag-evaluation-suite
    git diff --check

## Review gates

- The first test must fail because the new dataset is absent before corpus/data creation.
- Corpus content must be original; public references cannot be copied.
- The real loader is not mocked and the runner is used only with validate-only.
- The v2 hash values in prd.md must remain unchanged.
- Validation cannot be described as provider quality, production quality, capacity, compliance,
  or an M5/M7 pass.
- Independent label/content review is required before a separate real-provider task.

## Risk and rollback

| Area | Risk | Rollback |
| --- | --- | --- |
| New dataset | Incorrect anchors, labels, or implied copying | Revert it with corpus and provenance as one unit. |
| New corpus | Ambiguous policy text or unsafe-label mismatch | Correct before merge or revert the new corpus unit. |
| Focused tests | Coupling static data to runtime/provider behavior | Keep loader-only; revert assertions that cannot remain isolated. |
| Existing v2 | Baseline mutation | Do not edit; a changed pinned hash blocks completion. |

## Explicit no-go actions

- Do not run the evaluator without validate-only.
- Do not access staging, Kubernetes, real embedding/chat routes, object storage, or external data.
- Do not change M5/M7 gate state or treat synthetic results as closing evidence.
