# M7 Local Model Routing And Benchmark

## Goal

Make the existing strict model gateway explicit about provider identity, fallback,
health and failure state. Add deterministic benchmark contracts that can later be
filled with vLLM/GPU evidence without changing the Agent graph contract.

## Requirements

- **M7-R1**: A route descriptor records provider, model, revision, quantization,
  context limit and embedding dimension without exposing secrets.
- **M7-R2**: Retryable timeout, rate-limit, transport, 5xx, and exhausted bounded
  provider-output repair failures can use a configured fallback. Permanent auth,
  provider-envelope contract, authorization, and grounding failures do not silently
  switch providers.
- **M7-R3**: Circuit breaker implements CLOSED/OPEN/HALF_OPEN with bounded failure
  thresholds, cooldown and probe behavior, and is observable without high-cardinality labels.
- **M7-R4**: Provider health checks and model/embedding identity are included in reports.
- **M7-R5**: Embedding routes enforce dimension isolation and reject incompatible indexes.
- **M7-R6**: Benchmark reports include dataset/evidence hashes, latency, errors, fallback
  count, breaker state, citation validity, cost metadata and limitations.
- **M7-R7**: GPU/vLLM/quantization throughput and memory are explicitly excluded from the
  2026-08-14 release scope; no GPU capacity claim may be made. A future release may reopen
  this branch only with approved hardware and a pinned benchmark run.

## Acceptance Criteria

- [x] Primary/fallback routing and circuit transitions have deterministic unit tests.
- [x] OpenAI-compatible gateway remains strict and preserves stable error classes.
- [x] Benchmark schema validates route/model/quantization identity and target/measured
  separation.
- [x] Fallback never bypasses authorization, citation or approval gates.
- [x] External GPU/provider gates are explicit and not mislabeled as passed.
- [x] Prompt v8/v9 repairs an invalid citation identifier pair only when its verbatim
  excerpt belongs to exactly one authorized evidence item; absent, non-verbatim, and
  ambiguous matches remain invalid.
- [x] Exhausted provider-output schema repair can fall back within the shared route
  deadline while preserving observed provider identity and aggregated request, usage,
  token, repair, and fallback telemetry.
