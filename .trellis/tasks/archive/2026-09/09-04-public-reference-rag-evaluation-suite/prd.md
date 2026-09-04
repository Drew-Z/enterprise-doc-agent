# Public-reference-inspired RAG evaluation suite

## Goal

Add a separately versioned, traceable, fully synthetic RAG quality suite that broadens the
project's evaluation design around resilience, incident handling, access governance, and
untrusted-content safety. The suite prepares future human-reviewed real-provider evaluation; it
is not production evidence and cannot close the M5 or M7 external gates.

## Background and confirmed facts

- evaluation/rag_quality_v2.json is the current synthetic baseline. Local validation on
  2026-09-05 selected all 40 cases and passed with dataset SHA-256
  145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b and corpus SHA-256
  c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36.
- load_rag_quality_dataset already validates UTF-8 corpus files, stable-anchor quotes,
  uniqueness, category counts, answer/refusal contracts, and stable hashes. The new suite must
  reuse that contract and the existing runner.
- The project has no approved, de-identified representative enterprise corpus or independently
  reviewed labels that could satisfy a real-provider gate.
- Public material was reviewed only as scenario inspiration. The planned corpus uses new
  fictional names, facts, values, and wording. Its source boundary is recorded in
  research/public-reference-source-boundaries.md.

## Requirements

- PRR-R1 Source provenance: add repository provenance identifying the public references that
  informed scenario structure, their use boundary, and that no source text is copied.
- PRR-R2 Synthetic-only content: create four original UTF-8 plain-text fictional documents for
  Northstar Ledger. Do not include customer or personal data, real operational data, source-page
  wording, legal conclusions, or certification claims.
- PRR-R3 Independent versioning: add evaluation/rag_quality_public_reference_v1.json with
  corpus root corpus/public_reference_inspired_v1. It must load through the current schema
  without changing evaluator behavior.
- PRR-R4 Baseline immutability: leave evaluation/rag_quality_v2.json and
  evaluation/corpus/rag_quality_v1 byte-for-byte unchanged.
- PRR-R5 Coverage: define exactly 20 cases: six fact, six hard_negative, three refusal, two
  citation, and three safety.
- PRR-R6 Grounding: every answer case must require stable anchors from its own document and carry
  accepted and forbidden fact labels where it asserts facts. Every anchor quote must occur in its
  synthetic document.
- PRR-R7 Evidence-only refusal: each refusal case must contain no facts or expected citations and
  accept only the existing evidence-insufficiency refusal codes.
- PRR-R8 Indirect-injection safety: cover imported deletion instructions, credential or hidden
  instruction exfiltration, and encoded or multilingual content presented as instructions. Each
  case must require a grounded safe answer instead of executing or exposing anything.
- PRR-R9 Repository contract: focused tests must use the real loader and repository files to pin
  document keys, anchor IDs, case IDs, category distribution, refusal/safety contracts, and
  provenance limitations.
- PRR-R10 Local-only acceptance: run only static loader and validate-only checks. Do not call
  staging, object storage, embedding, chat, or any real provider.
- PRR-R11 Human-review boundary: labels and source provenance remain subject to independent human
  semantic review before any real-provider trial.
- PRR-R12 Truthful claims: state explicitly that this suite cannot demonstrate representative
  enterprise quality, production capacity or availability, privacy/compliance, provider
  stability/cost, or M5/M7 closure.

## Behavior slices

1. Independent dataset contract
   - Public interface: load_rag_quality_dataset(Path).
   - Action: load the new repository dataset and four corpus documents.
   - Expected: validation succeeds and produces dataset/corpus hashes distinct from v2.
   - Mock boundary: none; use the real loader, schema, and repository files.
2. Coverage and grounding contract
   - Public interface: loaded RagQualityDataset fields.
   - Action: inspect the 20 cases by category and their fact/anchor references.
   - Expected: exact 6/6/3/2/3 distribution; answers are grounded and refusals evidence-only.
   - Mock boundary: none.
3. Safety-label contract
   - Public interface: loaded safety cases and GoldenFact labels.
   - Action: inspect the three named indirect-injection scenarios.
   - Expected: each pins unsafe behavior as a forbidden answer and requires controlling anchors.
   - Mock boundary: no model, decoder, network, or staging invocation.
4. Non-regression validation
   - Public interface: scripts/evaluate_staging_rag_quality.py validate-only mode.
   - Action: validate the new suite and compare the unchanged v2 hash with its pre-task value.
   - Expected: both pass local schema/corpus validation without runtime service calls.
   - Mock boundary: no provider mock; validate-only output is the observable boundary.

## Acceptance criteria

- [x] Four original corpus documents exist under
      evaluation/corpus/public_reference_inspired_v1 with the 17 planned stable anchors.
- [x] evaluation/rag_quality_public_reference_v1.json loads and contains exactly 20 cases with
      category counts 6/6/3/2/3.
- [x] Every case references an existing document and anchor from that document, and every quote
      is present in its corpus file.
- [x] Answer cases require citations, factual answers have accepted/forbidden labels, refusals are
      evidence-only, and safety cases pin unsafe actions as forbidden answers.
- [x] Provenance states public-reference-inspired, synthetic, attribution-only, human-review
      required, and unable to close M5/M7.
- [x] Focused tests prove the new contract and preserve the pre-task v2 dataset/corpus hashes.
- [x] Both new and v2 datasets pass the runner validate-only mode.
- [x] Focused pytest, Ruff, Trellis validation, and git diff check pass.
- [x] No real provider, staging cluster, database, object store, or deployment is invoked.

## Out of scope

- Real-provider evaluation, provider cost collection, or any M5/M7 gate update.
- Acquisition or use of real enterprise/customer data.
- Runtime retrieval, prompt, agent, model route, safety policy, or evaluator-schema changes.
- Kubernetes deployment, UI/Figma work, GPU/vLLM work, SSO/ABAC expansion, and production
  capacity or disaster-recovery claims.

## Open questions

None block planning. Independent content and semantic review is a later execution gate before any
future real-provider experiment, not a prerequisite for constructing this synthetic suite.
