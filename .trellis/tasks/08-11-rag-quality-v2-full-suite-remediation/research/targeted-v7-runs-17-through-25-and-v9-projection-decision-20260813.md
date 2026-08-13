# Targeted V7 Runs 17 Through 25 And V9 Projection Decision 2026-08-13

## Evidence Boundary

This record contains only sealed report status, stable case IDs, allowlisted diagnostics,
stable-anchor resolution, metric outcomes, behavior versions, and hashes. It excludes answer text,
citation excerpts, candidate/runtime identifiers, credentials, URLs, object keys, and provider
payloads. Every prior Job, Pod, manifest, and report remains immutable.

## Runtime Identity

- Runtime commit: `a44038cdbf9504b5719eb52395b347be5abc0b44`.
- Behavior versions: graph `m4.v2`, prompt `m4.v7`, tool schema `m4.v2`.
- Evaluator: `m5.rag-quality.v4`.
- Dataset: `enterprise-rag-quality-v2`, SHA-256
  `145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b`.
- Corpus SHA-256: `c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36`.

## Sealed Targeted Reports

| Run | Gate | File SHA-256 | Payload SHA-256 | Safe finding |
| --- | --- | --- | --- | --- |
| `20260813-17` | failed | `e6a0d6cfda42733e0a4b17312dc61f2a1281616bf49e9c2f12b29e2ac28d290e` | `bbd612e40320b6f7190ce81aed56512972e7869b3c2c8c246a9ac346e9012e9e` | payment and retention forbidden facts |
| `20260813-18` | failed | `383d260c8e9349b2086cf22159dd48355e05ef60bd8d1bb7c9f9b73037e25362` | `ddefd9053a32c5546df4d47134cdeff8254f3f38c064d32d34ef61bb5656bb7d` | payment and retention forbidden facts |
| `20260813-19` | failed | `5991f4af5fc9bfac890cf6b2c46f2df50697ec9ef87c6d1bda099c8b6312cf59` | `b93c941577a05657d7b7a0521f540690255907a77937d7b2a3e9dcebb98677fb` | payment and retention forbidden facts |
| `20260813-20` | failed | `ec2cacaab56c7da5dcc506b19e26090eec2efcde8091176c476ea3b4f4f11bff` | `5d51e9f275a330f586453e723346caec690c6effea7607ce925e9be3938cf7bc` | payment and retention forbidden facts |
| `20260813-21` | failed | `6116c8089e0654627c30ed8fb20165b4c5661258be9e5e73f2b625aaa4658808` | `b82c066bbfe6f7717c910d50d4a7337cd89ba8394e731de5ecd18ceacd11bdf2` | payment unresolved citation and forbidden fact; retention forbidden fact |
| `20260813-22` | passed | `23e41db9f38b1281c95abace46285eec6ffc5277355fda86a593b000177ac43b` | `f3b25e5db51cb094980b4eb888424e63953bcdb8830598bfc9e1f238dff7bbb8` | all applicable metrics passed |
| `20260813-23` | failed | `abc0ff062b82544f1aeedb8f1843833e6a832c9746ae4ce0846d3f65578a896c` | `5f4e2893e3ce7fec3cdeaaaf217c95bc3c1be7e8fd916deedf6e400914a13cc5` | payment forbidden fact |
| `20260813-24` | failed | `50bce6875e2eca789b29b851a9e87b151f243ea6096b52918d4aca6cefb5e76a` | `753a0f4b996c875d167aa8a97780de2f88b1af535533543cd3aea41469aa4ba4` | payment unresolved citation; travel forbidden fact and extra anchor |
| `20260813-25` | failed | `f382955aed80e2ab215e96d80517ab4dfc22ae1f0b1243e80f9eec919a1c6a78` | `ed0c71cff7ea80fe98427a089419bd60e557f651def9aa4615913aa285fd4fde` | payment and travel forbidden facts |

## Confirmed Failure Shape

- Eight of nine aggregate gates failed; the scheduler stopped after `20260813-25` and no active
  targeted Job remains.
- The dominant failure is closed-label precision: the answer contains the accepted controlling
  fact and the citation resolves to the expected anchor, but the answer also repeats a forbidden
  conflict phrase. Payment exhibits this in seven runs, retention in five, and travel in two.
- Runs `-21` and `-24` also contain an unresolved payment citation. Run `-24` contains an extra
  travel anchor. These remain citation-quality failures and must not be hidden by answer handling.
- No supported retrieval separation defect is present. Dataset v2, anchors, RRF/top-k, thresholds,
  and deterministic citation authorization remain unchanged.
- Prompt v8 corrects only a known-chunk wrong-version transcription. It does not address this
  dominant answer-text variance, so deploying v8 alone is not a justified convergence strategy.

## V9 Projection Decision

Prompt behavior `m4.v9` adds deterministic direct-QA answer projection after identifier
normalization and provider citation repair. It is deliberately narrower than a semantic answer
repair:

- Apply only to a non-refusal `question_answer` payload.
- Require at least one citation, no duplicate chunk IDs, and every final citation pair to match
  frozen supplied evidence with a non-empty verbatim excerpt of at most 500 characters.
- Build `answer_text` from citation excerpts in citation order and deduplicate identical excerpts
  without reordering. Preserve citations and every other output field.
- When a cited excerpt explicitly labels balanced double-quoted content as untrusted, omit only the
  quoted span from projected answer text. Preserve the surrounding evidence statement, including
  any statement describing the quote's lack of effect.
- If any prerequisite is unprovable, keep the provider answer unchanged so the existing grounding
  boundary can reject invalid citations. Do not apply to summaries, structured extraction,
  refusals, unknown pairs, non-verbatim excerpts, empty citations, or duplicate chunks.
- Do not read evaluator cases, accepted answers, forbidden answers, stable anchors, or benchmark
  phrases in production logic.

This is a prompt behavior change only: graph remains `m4.v2` and tool schema remains `m4.v2`.
Rollback selects prompt `m4.v8`, restoring the provider answer while retaining v8 citation
normalization. Public gateway and grounding tests must prove both projection and every skip case.
