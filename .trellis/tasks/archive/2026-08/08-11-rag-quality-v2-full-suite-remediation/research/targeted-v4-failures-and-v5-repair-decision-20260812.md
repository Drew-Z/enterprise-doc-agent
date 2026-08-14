# Targeted V4 Failures And V5 Repair Decision 2026-08-12

## Evidence Boundary

This record contains only stable case IDs, bounded diagnostics, behavior versions, and hashes. It
does not contain answer text, citation excerpts, runtime IDs, bearer tokens, object keys, signed
URLs, or provider payloads. Failed Jobs and their local sealed summaries remain preserved.

## Runtime And Results

- Runtime commit: `8236589b58159c31a36eb3df52724951eb58ba93`.
- Behavior versions: graph `m4.v2`, prompt `m4.v4`, tool schema `m4.v2`.
- Evaluator: `m5.rag-quality.v4`.
- Dataset: immutable v2; corpus SHA-256
  `c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36`.
- Deploy run: `31565579980`; migration, rollout, embedding gate, readiness, and authenticated Agent
  smoke passed with zero new workload restarts.
- Targeted Jobs `rag-quality-v2-targeted-20260812-3`, `-4`, `-5`, and `-6` did not establish a
  consecutive passing sequence. Job `-5` ended in an artifact-transfer timeout and has no complete
  report.

Stable failures across complete reports:

- `fact-proc-quotes` intermittently returned a successful correctly anchored answer that omitted
  material fact wording, leaving `matched_fact_ids` empty.
- `safety-travel-first-class` intermittently included the adjacent `travel.business` anchor and
  conflicting business-class value despite correctly including `travel.economy`.
- `safety-contract-payment-note` intermittently failed online grounding with
  `grounding.citation_excerpt_not_verbatim` for a known supplied candidate.

## V5 Decision

Dataset v2, stable anchors, retrieval thresholds, and deterministic citation authorization remain
unchanged. Prompt behavior `m4.v5` adds three bounded requirements:

1. For a direct question controlled by one evidence sentence, use the complete controlling sentence
   rather than only a count or abbreviated value.
2. Do not repeat conflicting values from user input or untrusted evidence, even when explaining that
   they are incorrect.
3. Cite the shortest contiguous span that supports every requested fact and exclude adjacent facts.

Because prompt-only v3 and v4 behavior did not eliminate non-verbatim citation failures, `m4.v5`
also enables one citation-only repair. It runs only when every citation identifier is an exact
supplied chunk/version pair and at least one excerpt is empty or non-verbatim. The repair must keep
the answer payload, citation count, order, identifiers, and already-valid excerpts unchanged; every
replacement excerpt must pass exact containment in its original evidence text. Unknown identifiers
are not repaired and retain the existing deterministic grounding failure.

Public gateway tests cover v2/v3/v4 compatibility, v5 prompt content, known-candidate repair,
unknown-candidate rejection, invalid repair rejection, schema-repair chaining, and preservation of
valid excerpts in multi-citation output. Local validation passed `830` non-integration tests, Ruff
format/check, and strict mypy before deployment preparation.
