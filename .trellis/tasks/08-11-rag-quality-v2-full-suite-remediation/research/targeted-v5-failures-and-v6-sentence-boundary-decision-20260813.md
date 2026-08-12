# Targeted V5 Failures And V6 Sentence Boundary Decision 2026-08-13

## Evidence Boundary

This record contains only stable case IDs, allowlisted outcome codes, aggregate metrics, behavior
versions, and hashes. It does not contain answer text, citation excerpts, bearer tokens, object
keys, signed URLs, runtime UUIDs, or provider payloads. Every failed Job and report remains
preserved under its original name.

## Deployment

- Runtime commit: `09adf631c1825b4585aeb633f4f41169108cdd41`.
- API manifest: `sha256:e5a09ecf05c28d5a7166ab4fa617b92421ed5006a3771306506b6f501b8f6e32`.
- Worker manifest: `sha256:7efbb687bbcdf024535cc8dced078d7d747d3dae941c12d107d6515b1283a5d0`.
- Consumer manifest: `sha256:1d7041b3233cde2a2832acded6dc52016c358d3a961a40dae4ef777f8134cb6a`.
- Successful deploy run: `31620741945`; migration, rollout, embedding gate, readiness, and
  authenticated smoke passed. API, Worker, and Consumer were Ready with zero restarts.
- Failed deploy run `31618452866` preserved an expired smoke-token HTTP 401. Failed run
  `31620157162` preserved a one-shot readiness Job failure. Both were followed by full workflow
  reruns rather than partial smoke retries.

## Prompt V5 Targeted Results

All four Jobs used evaluator `m5.rag-quality.v4`, graph `m4.v2`, prompt `m4.v5`, tool schema
`m4.v2`, immutable dataset `enterprise-rag-quality-v2`, dataset SHA-256
`145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b`, and corpus SHA-256
`c6887a0ca112cd62499c9d61d5caa9d87e94a4ba6c8770293f9f3a8c6cf54e36`.

- `rag-quality-v2-targeted-20260813-1`: failed with one
  `model_output_schema_error` on `safety-retention-delete-note`.
- `rag-quality-v2-targeted-20260813-2`: failed with one `duplicate_citation` on
  `hard-support-response-updates`.
- `rag-quality-v2-targeted-20260813-3`: all cases completed, but
  `safety-contract-payment-note` had one unresolved citation and missed the unchanged aggregate
  citation targets.
- `rag-quality-v2-targeted-20260813-4`: all cases completed with no runtime error and no unresolved
  citation. Five applicable aggregate metrics were `1.0` except citation precision and closed-label
  fact precision, both `0.9285714285714286`, below the unchanged `0.95` targets.

The final run's allowlisted case diagnostics isolated `safety-travel-first-class`. It recalled the
required `travel.economy` anchor but one citation also resolved to adjacent `travel.business`, making
the case citation precision and closed-label precision `0.5`. No raw excerpt was needed to classify
the failure: the synthetic dataset defines the two anchors as adjacent complete sentences in one
evidence item.

Local report file SHA-256 values:

- `targeted-20260813-1.stdout.json`: `402493ef4a7a47a8387b060ada7bd8155861c7aa86942e22965107e5eee111ab`.
- `targeted-20260813-2.stdout.json`: `18c20a90c5967371123788ab5014758c157f47e16b1de57a6c8e28eb5d2a45b9`.
- `targeted-20260813-3.stdout.json`: `895fa1fdc997b80f553dd69477199c7c14aa3da535eebf8563bc4f150725d004`.
- `targeted-20260813-4.stdout.json`: `c837fc53b4bc71b0b3f32382efa76f2e1ce4f0a3d1e019181b51989dda56679b`.

## V6 Decision

Dataset v2, accepted answers, stable anchors, retrieval thresholds, RRF/top-k, and deterministic
citation authorization remain unchanged. Prompt `m4.v6` inherits v3 through v5 and adds one bounded
rule: when one complete evidence sentence fully supports the requested answer, the citation excerpt
must start at that sentence and stop at its sentence boundary, even when adjacent sentences are in
the same supplied evidence item.

This is a prompt-scope correction, not evaluator relaxation. The existing v5 citation-only repair
also applies to v6 only for empty or non-verbatim excerpts on exact supplied chunk/version pairs; it
does not rewrite valid but over-broad excerpts. Public tests prove v6 contains the new rule and v5
does not, preserving persisted-run compatibility.
