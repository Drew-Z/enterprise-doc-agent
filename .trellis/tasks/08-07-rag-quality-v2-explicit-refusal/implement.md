# Implementation Plan: RAG Quality V2 and Explicit Model Refusal

## Ordered Checklist

- [x] Add a public scoring test for one citation spanning two anchors; confirm red.
- [x] Implement multi-anchor resolution and bounded citation precision; confirm green.
- [x] Add public scoring tests for standalone short numeric matches and supersets; confirm red.
- [x] Implement normalized boundary-safe variant matching; confirm green.
- [x] Copy immutable v1 to v2 and change only version/reviewed accepted variants.
- [x] Add dataset validation tests proving v1 and v2 shape and independent hashes.
- [x] Add provider contract tests for valid refusal, malformed refusal, task mismatch, and
      continued answer citation requirements; confirm red.
- [x] Implement strict answer/refusal schemas, prompt, parsing, and exported contracts.
- [x] Add grounding and graph tests for model refusal after accepted retrieval; confirm red.
- [x] Implement grounding union return and conditional graph route; confirm green.
- [x] Bump graph/prompt/tool behavior defaults and update affected compatibility assertions.
- [x] Run focused core, worker, API, and integration tests.
- [x] Run Ruff, mypy, secret scan, report seal verification, and the non-integration suite.
- [x] Inspect and record sanitized stock-price retrieval diagnostics.
- [ ] Build/push/deploy the staging image and verify rollout/health.
- [ ] Run the four remediation cases three times each into new v2 evidence.
- [x] Preserve the first two passing repeats and the third repeat's real MCP timeout failure.
- [x] Add regression coverage and repair immediate search cancellation retry, stale lease fencing,
      and retryable MCP error classification.
- [x] Preserve targeted repeats four through eight, including both transient provider failures.
- [x] Add a bounded Agent execution retry setting and apply it consistently to initial and
      approval-resume jobs; set the staging budget to five.
- [ ] On success, run the 12-case v2 trial and the 40-case full suite.
- [ ] Review reports, update task/gate records truthfully, validate Trellis, commit, and push.

## Validation Commands

```powershell
python -m pytest packages/core/tests/test_rag_quality_evaluation.py -q
python -m pytest packages/core/tests/test_model_gateway.py packages/core/tests/test_grounded_answer.py packages/core/tests/test_agent_graph.py -q
python -m ruff check .
python -m mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
python -m pytest -m "not integration" -q
python ./.trellis/scripts/task.py validate 08-07-rag-quality-v2-explicit-refusal
```

## Review Gates

- The v1 JSON and prior evidence must show no diff.
- A valid model refusal bypasses artifacts; an invalid answer still fails citation validation.
- Citation precision never exceeds one.
- Retrieval thresholds remain unchanged without cross-case evidence.
- Real evaluation starts with targeted cases and remains sequential.

## Rollback Points

- Before runtime deployment: revert the remediation commit only.
- After deployment but before trials: redeploy the prior image digest.
- After trials: preserve v2 reports as failed/partial evidence; never rewrite them as passing.
