# Prompt V8 Known-Chunk Version Normalization Local Validation 2026-08-13

## Scope And Decision

Targeted Prompt v7 run `20260813-15` failed because the model returned a wrong document version
for an otherwise supplied chunk. Existing citation-only repair intentionally skipped every unknown
`(chunk_id, document_version_id)` pair, so the downstream authorization boundary rejected the
output with `grounding.citation_wrong_version`.

Prompt behavior `m4.v8` adds one deterministic local normalization before the existing provider
citation repair. It corrects only `document_version_id` when all of the following are true:

- the proposed `chunk_id` occurs exactly once in the frozen supplied evidence;
- the proposed excerpt is non-empty and already a verbatim span from that same evidence item;
- the proposed pair is not already valid.

It does not select a different chunk, use an excerpt or version alone to infer a candidate, change
answer text, change citation order/count, or add a provider call. Unknown chunks, duplicate chunk
ambiguity, and non-verbatim excerpts remain unchanged for the existing deterministic authorization
boundary to reject. Dataset v2, stable anchors, retrieval thresholds, RRF/top-k, graph behavior,
tool schema, and citation authorization are unchanged.

## Red And Green Evidence

- The initial focused run failed on the new known-chunk wrong-version behavior and the expected
  default prompt-version assertion. Changing the test default to v8 also demonstrated that v8 had
  not yet inherited the existing v3-v7 prompt and citation-repair rules.
- The implementation added the bounded normalization, inherited the existing v3-v7 behavior for
  v8, and bumped only the default prompt version from `m4.v7` to `m4.v8`.
- Public gateway tests prove one provider call, preserved answer/excerpt/chunk, corrected version,
  and successful passage through `validate_grounded_output` and the unchanged authorization gate.
- Negative tests prove v7 compatibility, unknown-chunk preservation, non-verbatim preservation,
  and duplicate supplied-chunk ambiguity preservation.

## Local Validation

- Focused gateway and Agent settings tests: `44 passed`.
- Full Ruff format check: `319 files already formatted`.
- Full Ruff check: passed.
- Strict mypy: `131 source files`, no issues.
- Full non-integration suite: `836 passed`, `107 deselected`.
- Evaluator/report sealing and secret-safe diagnostic tests: `18 passed`.
- Trellis task context validation: passed (`implement.jsonl` 10 entries, `check.jsonl` 11 entries).
- Immutable v1/v2 datasets and pre-task evidence: `31` checked, `0` SHA-256 mismatches.
- Current diff credential-signature scan: `0` matches for private keys, common cloud tokens,
  GitHub tokens, bearer JWTs, and common API-key signatures.

## Remaining External Gates

- Commit and GitHub Quality have not yet run for this behavior slice.
- No immutable Prompt v8 image has been published or deployed.
- The targeted gate must restart with a new resource name after deployment. Run `20260813-16`
  established one consecutive Prompt v7 aggregate pass, but it does not count toward a Prompt v8
  three-pass sequence because the runtime behavior changed.
- After three consecutive targeted Prompt v8 passes, run the bounded 12-case gate once and then
  the full 40-case gate until three consecutive reports pass on the same immutable image.
- Closed-label payment and retention wording variance and the isolated schema-repair exhaustion
  remain provider-generation findings. Production code does not import evaluation-only forbidden
  phrases or special-case those benchmark cases.
