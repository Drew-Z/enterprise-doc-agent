# M7 Local Model Routing And Benchmark

## Goal

Make the existing strict model gateway explicit about provider identity, fallback,
health and failure state. Add deterministic benchmark contracts that can later be
filled with vLLM/GPU evidence without changing the Agent graph contract.

## Requirements

- **M7-R1**: A route descriptor records provider, model, revision, quantization,
  context limit and embedding dimension without exposing secrets.
- **M7-R2**: Retryable timeout, rate-limit and 5xx failures can use a configured fallback;
  permanent auth/schema failures do not silently switch providers.
- **M7-R3**: Circuit breaker implements CLOSED/OPEN/HALF_OPEN with bounded failure
  thresholds, cooldown and probe behavior, and is observable without high-cardinality labels.
- **M7-R4**: Provider health checks and model/embedding identity are included in reports.
- **M7-R5**: Embedding routes enforce dimension isolation and reject incompatible indexes.
- **M7-R6**: Benchmark reports include dataset/evidence hashes, latency, errors, fallback
  count, breaker state, citation validity, cost metadata and limitations.
- **M7-R7**: GPU/vLLM/quantization throughput and memory remain `blocked_external` until
  a real hardware run supplies evidence.

## Acceptance Criteria

- [x] Primary/fallback routing and circuit transitions have deterministic unit tests.
- [x] OpenAI-compatible gateway remains strict and preserves stable error classes.
- [x] Benchmark schema validates route/model/quantization identity and target/measured
  separation.
- [x] Fallback never bypasses authorization, citation or approval gates.
- [x] External GPU/provider gates are explicit and not mislabeled as passed.
