# Implementation Plan: RAG Quality V2 Full-Suite Remediation

## Pre-Implementation Gate

- [x] User reviews and explicitly approves `prd.md`, `design.md`, and this plan.
- [x] Keep the task in `planning`; run `task.py start` only after approval.
- [x] Snapshot SHA-256 for v1/v2 datasets and all existing full-suite evidence before edits.
- [x] Confirm the two unrelated untracked `docs/ops/*.md` files remain outside this task.

## Slice 1: Evaluator Diagnostics

- [x] Add focused scorer tests that construct resolved, unresolved, expected, and unexpected
      citation anchors and assert the new safe diagnostic values; confirm red.
- [x] Add immutable per-citation diagnostics to `RagQualityCaseScore` without changing existing
      scoring equations or pass semantics; confirm green.
- [x] Add staging evaluator tests for the serialized safe fields and allowlisted attempt
      diagnostic; seed raw answer, citation, chunk, UUID, URL, token, and MCP message values and
      assert none are present; confirm red then green.
- [x] Add an explicit evaluator/report behavior version to distinguish the new report shape.

## Slice 2: Grounding Failure Taxonomy

- [x] Add public `validate_citations`/`validate_grounded_output` tests for unknown chunk, wrong
      version, unauthorized candidate, empty excerpt, too-long excerpt, and non-verbatim excerpt;
      assert parent public codes and distinct safe diagnostics; confirm red.
- [x] Introduce the typed internal citation-validation error and preserve the current
      authorization order and public error codes; confirm green.
- [x] Add Agent execution tests proving only typed grounding diagnostics cross the handler
      boundary and unknown exceptions remain generic.

## Slice 3: Durable Attempt Diagnostics

- [x] Add a migration/model test for nullable `job_attempts.diagnostic_code`; confirm red.
- [x] Add the forward-only additive migration and ORM field; confirm green on fresh migration and
      upgrade from the previous head.
- [x] Extend `JobRuntimeService.fail` and queue settlement tests so a bounded diagnostic is written
      to the current fenced attempt for retryable, permanent, and exhausted outcomes.
- [x] Extend Core Agent status and API response tests for null and populated `diagnosticCode`.
- [x] Verify the field never enters run events, logs, or Prometheus labels.

## Slice 4: MCP Failure Taxonomy

- [x] Add `ClientSession.call_tool` boundary tests for each relevant tool operation, recognized
      retryable/permanent subcodes in text and structured payloads, unknown codes, and malicious
      strings; confirm red.
- [x] Pass the known tool operation into result classification and construct only allowlisted
      diagnostics. Preserve existing public code and retryability behavior; confirm green.
- [x] Verify `search_document` and `create_draft_artifact` wrappers propagate the correct bounded
      diagnostic through queue settlement and Agent status.

## Slice 5: Diagnostic Staging Run

- [x] Run focused tests, Ruff format/check, mypy, migration checks, secret scan, report tests, and
      the non-integration suite.
- [x] Commit and push the diagnostic slice after the normal one-shot commit review.
- [ ] Publish and deploy one immutable diagnostic image; record commit, digest, route identity,
      graph/prompt/tool/evaluator versions, readiness, and workload restarts.
- [x] Run the seven selected cases in the order documented in `design.md`; write a new sealed
      report and verify its seal.
- [x] Review the synthetic quote-count answer and any unresolved synthetic citation through the
      authenticated artifact path. Record only conclusions/approved variants in task research.
- [x] Update `research/full-suite-failure-taxonomy.md` with confirmed diagnostics and selected
      branches before modifying semantic behavior.

## Slice 6: Evidence-Gated Remediation

- [x] For every confirmed branch, first add a public-interface regression test that fails for the
      observed reason.
- [ ] Dataset branch, if selected: create v3 from byte-identical v2 content except version and
      reviewed accepted variants; prove v1/v2 hashes unchanged, v3 hash distinct, corpus hash
      unchanged, and all three datasets load.
- [ ] Anchor branch, if selected: implement only the reviewed deterministic evaluator mapping and
      prove it does not map adjacent/unrelated clauses or alter online authorization.
- [x] Prompt branch, if selected: require minimum sufficient citations and verbatim supplied
      identifiers/excerpts; bump prompt version and cover single- and multi-anchor cases. After
      the first targeted repeat exposed two abbreviated facts and one repeated untrusted value,
      add Prompt v4 standalone-fact and conflict-control guidance with public regression tests.
      After final Prompt v5 staging run `20260813-4` isolated one citation spanning two adjacent
      travel-policy sentences, add Prompt v6's explicit sentence-boundary rule without changing
      retrieval, anchors, thresholds, or citation authorization. After Prompt v6 runs
      `20260813-5` and `-6` proved that travel case fixed but exposed duplicate citation pairs and
      repeated untrusted actions, add Prompt v7's bounded pair-uniqueness and conflict-scope rules.
- [x] Evaluator sample branch: record `applicable_targets` and apply unchanged thresholds only to
      metrics represented by the selected sample; prove the complete suite still requires all
      eight targets and bump the evaluator behavior version.
- [x] Citation-repair branch, only if prompt remains insufficient: permit one citation-only repair
      for known candidates, preserve all non-citation output, reject unknown IDs, re-run the same
      authorization gate, and bump graph/prompt versions.
- [ ] MCP branch, if selected: fix the owning deterministic tool failure and preserve retryable
      classification for infrastructure errors.
- [ ] Retrieval branch, only with frozen miss evidence: test keyword/vector ranks and RRF/top-k
      through `RetrievalService.retrieve`, then make the smallest cross-case-supported change.
- [x] Update task research with each accepted/rejected branch and its test/evidence reference.

## Slice 7: Local And CI Validation

- [x] Run all focused Core/Worker/API/evaluator/migration tests.
- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src`.
- [x] Run the repository secret scan and report seal/payload validators.
- [x] Run `uv run pytest -m "not integration"`.
- [x] Run required database-backed integration tests with the local Compose/PostgreSQL boundary.
- [x] Push only after the one-shot commit plan is approved; verify GitHub Quality succeeds.

## Slice 8: Real-Staging Quality Gate

- [x] Preserve failed deploy runs `31550723096` and `31551145786`. Confirm the first
      readiness smoke was transient, then reproduce the second failure as the authenticated
      smoke client's 30-second per-request timeout against a successful 63-second ready-version
      query. Raise only the bounded smoke request timeout to 90 seconds with a red/green test.
- [x] Preserve failed deploy run `31552350141`. Confirm upload, ingestion, and readiness passed,
      then classify the Agent failure as repeated Worker-side `mcp_client_timeout` while
      `create_draft_artifact` remained running. Set the staging MCP client/server/stale-recovery
      timeout to one reviewed 90-second value with a final-render red/green contract test.
- [ ] Publish and deploy one immutable final image. Confirm readiness and zero unexpected restarts.
- [ ] Run the seven-case targeted set until three consecutive complete reports pass. Preserve all
      failed/transient/interrupted evidence and reset the sequence after each failure.
- [ ] Run and seal the bounded 12-case suite once; require all approved targets.
- [ ] Run and seal the complete 40-case suite. Require every unchanged aggregate target.
- [ ] Repeat the full suite until three consecutive complete reports pass on the same image,
      runtime route, behavior versions, and dataset version.
- [ ] Verify every new payload seal and record evidence hashes and GitHub/deployment references.

## Final Review

- [x] Run a full-scope Trellis check across Core, Worker, API, migration, evaluator, and evidence
      changes; fix findings and repeat gates.
- [x] Confirm v1/v2 datasets and all pre-task evidence hashes are unchanged.
- [x] Confirm no raw model/tool/document/runtime/secret data appears in code-generated reports,
      events, logs, or metrics.
- [x] Decide whether the diagnostic contract or failure taxonomy adds non-obvious knowledge to
      `.trellis/spec/`; update specs in the same commit batch when required.
- [x] Record residual synthetic-corpus, human-review, model revision, cost, capacity, and
      production limitations; do not mark M5/M7 complete without independent evidence.

## Risky Files And Rollback Points

- `packages/core/src/enterprise_doc_core/documents/retrieval.py`: authorization boundary; never
  relax candidate, tenant/version, or verbatim excerpt checks.
- `packages/core/src/enterprise_doc_core/evaluation/rag_quality.py`: score semantics and report
  comparability; diagnostic slice must not alter metrics.
- `packages/core/src/enterprise_doc_core/jobs/`: fencing and retry persistence; diagnostic writes
  must target only the claimed attempt.
- `apps/worker/src/enterprise_doc_worker/mcp_client.py`: retry classification and secret boundary;
  allowlists only.
- Agent behavior-version constants/settings: graph rollback requires matching old code and version.
- Evaluation datasets/evidence: never edit v1/v2 or overwrite reports; use new versioned paths.
