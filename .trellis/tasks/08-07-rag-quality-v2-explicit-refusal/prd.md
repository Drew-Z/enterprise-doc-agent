# RAG Quality V2 and Explicit Model Refusal

## Goal

Correct the deterministic evaluator false negatives discovered by the bounded real-provider
trial and add a first-class model refusal outcome so unsupported questions terminate as
`refused` instead of failing the grounding gate. Preserve the immutable v1 dataset and
evidence while producing independently versioned v2 inputs and reports.

## Requirements

- Keep `evaluation/rag_quality_v1.json`, its sealed reports, and all existing v1 evidence
  byte-for-byte unchanged.
- A single runtime citation excerpt may resolve to every stable anchor it actually covers.
  Citation precision must remain bounded to `[0, 1]` when one citation covers multiple
  anchors, and citation recall/grounding must credit each supported expected anchor.
- Accepted and forbidden answer variants must use normalized, boundary-safe matching.
  Short numeric labels such as `15` must match a standalone answer but not `150`.
- Add `evaluation/rag_quality_v2.json` with the same 40 cases and corpus, a new version ID,
  and reviewed accepted variants for the three semantically correct v1 false negatives.
- The model output contract must represent exactly two outcomes: a grounded answer or an
  explicit refusal. A model refusal may use only `insufficient_evidence`, contain no answer,
  structured fields, citations, or risk hint, and must still match the requested task type.
- A valid model refusal must route through `finalize_refused`; it must not create or publish
  an artifact. Invalid answer payloads, missing citations on answer outcomes, unauthorized
  citations, and provider schema failures must remain failures.
- Version the changed graph, prompt, and output/tool contract so new runs cannot be confused
  with the v1 behavior contract.
- Diagnose the stock-price case retrieval candidates and scores before changing retrieval
  thresholds. Do not tune a global threshold from one synthetic example.
- Store every remediation trial under a new v2 evidence filename. Never overwrite v1 files.
- Keep the Agent execution retry budget explicit and bounded. Initial executions and
  approval resumes must use the same configured value; staging may raise the default
  three attempts to five for observed transient provider failures.
- Reports must remain secret-safe and omit raw answers, document text, URLs, credentials,
  runtime UUIDs, and provider response bodies.

## Behavior Slices

1. Evaluator anchor mapping.
   Public interface: `score_rag_quality_case`.
   Input: one citation excerpt spanning two expected anchors.
   Outcome: both anchors and facts are credited, and precision is at most one.
   Mock boundary: none.
2. Evaluator variant matching.
   Public interface: `score_rag_quality_case`.
   Input: standalone short numeric/phrase answers and deceptive supersets.
   Outcome: reviewed variants match while token supersets do not.
   Mock boundary: none.
3. Model output contract.
   Public interface: `OpenAICompatibleChatGateway.generate` and model schemas.
   Input: answer, valid refusal, malformed refusal, and mismatched task payloads.
   Outcome: only contract-valid answer/refusal objects survive parsing and one bounded repair.
   Mock boundary: HTTP provider only.
4. Grounding and graph routing.
   Public interface: `validate_grounded_output` and `build_agent_graph`.
   Input: accepted retrieval followed by a valid model refusal.
   Outcome: run finalizes refused without citation validation or artifact writes.
   Mock boundary: graph backend/checkpointer and fixed model gateway.
5. Real staging remediation.
   Public interface: staging quality evaluator CLI.
   Input: v2 dataset and real staging services.
   Outcome: four prior failures repeat three times, followed by bounded and full suites when
   the earlier gates pass.
   Mock boundary: none; provider and object store are real.
6. Agent execution retry budget.
   Public interface: `AgentSettings`, `AgentRunService.create`, and `ApprovalService`.
   Input: a configured execution-attempt limit.
   Outcome: initial and resumed Agent jobs persist the same bounded retry limit.
   Mock boundary: database-backed job creation only; provider retries remain real in staging.

## Acceptance Criteria

- [ ] Focused tests fail on the v1 implementation for multi-anchor mapping, numeric boundary
      safety, valid model refusal parsing, grounding, and graph routing.
- [ ] Focused tests pass after implementation and preserve answer citation enforcement.
- [ ] `rag_quality_v2.json` validates with 8 documents, 40 cases, 12 trial cases, and a hash
      distinct from v1 while both v1 and v2 remain loadable.
- [ ] The stock-price retrieval diagnosis records sanitized scores/anchor identifiers and a
      threshold decision without leaking raw production identifiers or secrets.
- [ ] Ruff, mypy, secret scan, report sealing checks, focused tests, and the non-integration
      suite pass locally.
- [ ] The four formerly failing cases are run three times each against staging and recorded in
      a new v2 remediation report.
- [ ] Passing and failed remediation repeats remain immutable, including transient provider
      failures, and the bounded staging retry budget is covered by job and app-wiring tests.
- [ ] If the targeted gate passes, the 12-case bounded v2 trial and then the full 40-case v2
      suite are executed without overwriting prior evidence.
- [ ] M5/M7 remain open unless all of their independent evidence requirements are genuinely
      satisfied; no quality result is relabeled as production proof.
- [ ] The final changes are committed and pushed from `main` without adding the two unrelated
      untracked documentation files.

## Notes

- The first real trial established that three failed answer cases were semantically correct
  and one unsupported stock-price question incorrectly reached `citation_required`.
- Real-provider repetition is ordered and bounded to respect the upstream concurrency limit.
- Release `v0.1.24` is deployed to staging with the Agent execution retry budget set to five.
  Targeted repeat 9 passed all four cases and its report seal is valid, but it establishes only
  one consecutive pass because repeat 8 remains an immutable transient provider failure.
- The next targeted attempt did not produce a report because newly uploaded documents failed
  ingestion when the configured embedding endpoint returned HTTP 402. The 12-case and 40-case
  gates remain blocked until embedding service is restored and three consecutive targeted
  reports pass.
