# Real Provider RAG Quality Evaluation

## Goal

Create a versioned, repeatable, secret-safe quality evaluation for the real staging
upload, ingestion, embedding, hybrid retrieval, grounded Agent, and citation path.
The result must distinguish measured real-provider behavior from deterministic tests
and from still-unverified production claims.

## Requirements

- Provide a synthetic enterprise corpus with no customer or personal data.
- Provide exactly 40 versioned golden cases:
  - 18 single-document factual questions.
  - 8 hard-negative or ranking questions.
  - 6 unsupported questions that should be refused.
  - 4 citation-focused questions.
  - 4 prompt-injection or instruction-conflict questions.
- Identify evidence with stable anchors composed from document key, section or page,
  and a quoted source span. Runtime database UUIDs must not be golden labels.
- Validate dataset uniqueness, cross-references, path containment, corpus hashes, and
  quoted source spans before any network call.
- Measure retrieval/answer quality with deterministic labels: fact recall,
  closed-label fact precision, grounded-fact rate, citation precision, citation recall,
  refusal precision, refusal recall, and refusal-reason accuracy.
- Measure end-to-end duration and classify terminal/provider errors without exposing
  secrets or raw provider response bodies.
- Reuse the authenticated staging upload and Agent APIs. The runner must support case
  filtering and a bounded sample count so a low-cost trial can precede the full suite.
- Reports must omit tokens, API keys, endpoint URLs, presigned URLs, absolute local
  paths, document bodies, and complete model answers. Store hashes and label matches.
- Provider/model and behavior-version identity may be recorded only from public,
  already-sanitized runtime fields.
- Existing deterministic M3/M4/M5/M7 reports remain valid and must not be relabeled as
  real-provider evidence.
- M5/M7 manual gates may close only when every required evidence item is genuinely
  satisfied. Partial real-provider results update blocking reasons and prerequisites
  without fabricating cost, repeatability, representative-corpus, or human-review data.

## Behavior Slices

1. Public interface: golden-set loader.
   Input: dataset JSON and its corpus directory.
   Outcome: typed dataset plus stable hashes, or a deterministic validation error.
   Mock boundary: none; use temporary files only.
2. Public interface: case scorer and aggregate scorer.
   Input: golden case plus sanitized observation.
   Outcome: deterministic fact, citation, grounding, and refusal metrics.
   Mock boundary: none.
3. Public interface: staging quality runner.
   Input: validated dataset, authenticated staging client, case selector, deadline.
   Outcome: documents are uploaded once, selected cases reach terminal outcomes, and a
   secret-safe report is returned.
   Mock boundary: HTTP/object-store client, time, and sleep.
4. Public interface: CLI/evidence writer.
   Input: endpoint/allowlists on CLI and token from environment.
   Outcome: sealed JSON report with sanitized provenance and non-zero exit on failed or
   incomplete target runs.
   Mock boundary: environment and staging client.

## Acceptance Criteria

- [x] The 40-case dataset and all corpus anchors pass schema and source-span validation.
- [x] Focused tests prove invalid references, path traversal, duplicate keys, and missing
      quote spans are rejected before network access.
- [x] Focused tests prove missing citations are not rewarded and unsupported answers do
      not count as correct refusals.
- [x] Focused tests prove runtime chunk UUID changes do not affect stable-anchor scoring.
- [x] The runner supports a dry validation mode, selected case IDs, bounded sample count,
      and deterministic idempotency keys derived without revealing content.
- [x] A fake-client end-to-end test covers upload, readiness polling, Agent completion,
      artifact integrity verification, citation mapping, and refusal handling.
- [x] A generated report contains no secret values, URLs, presigned URLs, absolute paths,
      raw document text, or complete answer text.
- [x] Ruff, mypy, focused tests, and the non-integration suite pass.
- [x] A bounded real staging trial is executed when the saved credentials and staging
      route are available; otherwise the report is explicitly `blocked_external`.
- [x] M5/M7 gate records remain open unless all required evidence is present.

## Notes

- Fact precision is a closed-label deterministic metric: it detects expected and known
  contradictory labels but cannot prove that arbitrary free-form claims are correct.
  The limitation must be present in every report.
- Full human semantic review remains a separate manual requirement.
