# Targeted V7 Runs 7 Through 16 And V8 Repair Decision 2026-08-13

## Evidence Boundary

This record uses only sealed report status, stable case IDs, allowlisted outcome and diagnostic
codes, aggregate metrics, behavior versions, provider route labels, and hashes. It excludes answer
text, citation excerpts, candidate/runtime identifiers, tokens, object keys, URLs, and provider
payloads. Every failed Job, Pod, and report remains preserved under its original name.

## Runtime And Evaluation Identity

- Runtime commit: `a44038cdbf9504b5719eb52395b347be5abc0b44`.
- Final deployment workflow: `31657053341`; migration, rollout, embedding gate, readiness, and
  authenticated smoke passed with zero new Pod restarts.
- Behavior versions: graph `m4.v2`, prompt `m4.v7`, tool schema `m4.v2`.
- Evaluator: `m5.rag-quality.v4`.
- Dataset: `enterprise-rag-quality-v2`, SHA-256
  `145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b`.
- Corpus SHA-256: `c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36`.
- Provider route: `openai_compatible` / `grok-4.5`; provider revision was not exposed.

## Sealed Targeted Reports

The top-level report `status` is the gate result. For a bounded selected sample, the evaluator
applies each unchanged threshold only when that metric is represented by the selected cases. A
single case may therefore have `passed=false` while the aggregate targeted report still passes its
approved threshold. This is the evaluator behavior approved in `m5.rag-quality.v4`.

| Run | Gate | File SHA-256 | Payload SHA-256 | Safe finding |
| --- | --- | --- | --- | --- |
| `20260813-7` | failed | `9e4777c6b2d2d18dff4289c016990a93cd372ca576a3f4052cf1ab39c317a718` | `6d7109c38391e76ee9af3096822e626ce727dea2f4b2f4fea6d854887413066a` | payment: `grounding.citation_chunk_not_in_candidates` |
| `20260813-8` | failed | `4c38888a0187781d3d0ddf9f969d8f4faca108875d45adea52265e15396ab7fe` | `168a3c8bafd99f1c2d0aefb07f1523c452d4f8bd4e3cb51f07119d302e9471f2` | payment and retention closed-label precision; travel extra anchor |
| `20260813-9` | passed | `ad0ec43290ff23646a46d3eb1b1be19bdb1d0fb4736c50d964c049a033d04479` | `38b89c00b6d69d79e8c6a19cf7920c6218eee3d2c68e7ab6d50c6511186990f9` | all applicable metrics `1.0` |
| `20260813-10` | failed | `fa1c927dc7ecb9dc338220bd8881fa41f36df37c2fd15e69ea1f25b2cc4734b3` | `b3437bede1204c29908351728a74c27e42808ac179fd3a61552c2d2934a4cc90` | payment and retention closed-label precision |
| `20260813-11` | failed | `b9742b0f603e0ac1dad6aae55bce6db3cbccda22b2b195bfe10a9ea7ea3462ab` | `d2eab173111be6bc474fd0416c319a00ebb9082d389e65c06d24a87c49f8f31c` | payment and retention closed-label precision |
| `20260813-12` | failed | `f134a48b1fe88c700931de1179c6af1583e465f7778815565a3f80025fa06021` | `21972c65518ef5cffff966abcdd2ba185f84085fffa3e8e3560ef4c6684a895e` | support: `model_output_schema_error` |
| `20260813-13` | failed | `de7b3ae4ab353f78b561e1a944b11c90676eeab2f85dd752381477d324feb89d` | `c1450b2e9b1f2b466adc569ca398d618c595dc9127ae7d7fc7575fcbb0c6c244` | payment and retention closed-label precision |
| `20260813-14` | passed | `0443ef4f1a1b40cad18425b0fc79415b9fe856a57410419b71f2239b6ddb1fb8` | `79b7c3b41133c9175bb6318c4d3d740bc6f7594ced377d15d9e3f8b0a05ceba8` | applicable thresholds passed; retention case precision `2/3` |
| `20260813-15` | failed | `901d480a68c32aeb075303864590d141e17adc9e2d68224921370ef75b1773ea` | `2ce3158da948e3ca2c88b5584ef1373a7e630fdfaa0841f08e46af750733130b` | remote days: `grounding.citation_wrong_version` |
| `20260813-16` | passed | `1b03885cc3ec51f8e39eb60ca171c3075df8bfbc5127e260c3d3f6db46ee965c` | `5fa4690a7d6c6eab87ef898e9df138a421fa499d9d5e947b8fba568ce5a8a6fc` | applicable thresholds passed; retention case precision `2/3` |

The current consecutive targeted gate count is therefore **one**, established by
`20260813-16`. Run `20260813-15` reset the sequence. Runs `-14` and `-16` are not failures merely
because one individual case did not achieve perfect closed-label precision; both top-level reports
met every applicable unchanged aggregate target.

## Confirmed Residual Failure Classes

1. Citation identifier transcription remains outside the existing citation-only repair. The
   repair is currently disabled when any proposed `(chunk_id, document_version_id)` pair is not
   already present in supplied evidence. This leaves the observed unknown-chunk and wrong-version
   cases to fail at the downstream authorization boundary.
2. Strict model output schema repair is already bounded to one attempt, but one support case still
   exhausted it. This is preserved as provider-generation evidence; it does not justify job-level
   retries or schema relaxation.
3. Prompt v7 substantially improves minimum citations and conflict scope but does not eliminate
   closed-label safety wording variance. Production code must not import evaluation-only forbidden
   phrases or special-case the payment and retention benchmarks.
4. No report shows a supported retrieval separation defect. Dataset v2, RRF/top-k, retrieval
   thresholds, stable anchors, and citation authorization remain unchanged.

## V8 Repair Decision

The next selected behavior slice is a deterministic, evidence-bounded citation identifier
normalization before provider citation repair:

- A malformed proposed pair may be normalized only when its chunk ID already identifies exactly
  one supplied evidence item and the proposed non-empty excerpt is a verbatim span from that item.
- The normalization may recover only that known chunk's supplied document version. It never uses
  a document version or excerpt alone to select a different chunk.
- Unknown chunk IDs, duplicate supplied chunk ambiguity, and non-verbatim excerpts remain
  unchanged and fail at the existing authorization boundary.
- The normalization may change citation identifiers only. It may not change answer text, task
  fields, citation order/count, or already valid pairs.
- The normalized output still passes the unchanged deterministic citation authorization boundary.
  No candidate is introduced beyond the frozen supplied evidence.
- Ambiguous or unprovable cases remain permanent failures.

This behavior requires public red/green gateway tests and a prompt/graph behavior review. Because
the graph shape and provider calls do not change, the expected version bump is prompt `m4.v8`; the
graph remains `m4.v2` unless implementation evidence proves otherwise.
