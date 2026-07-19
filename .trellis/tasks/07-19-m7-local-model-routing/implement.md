# M7 Local Model Routing And Benchmark: TDD Implementation Plan

## Slice 0: Route And Benchmark Schemas

- [x] Add typed provider route, circuit state and benchmark report contracts.
- [x] Add identity redaction and dimension compatibility tests.

## Slice 1: Fallback And Circuit Breaker

- [x] Implement retryable-only fallback and CLOSED/OPEN/HALF_OPEN transitions.
- [x] Add deterministic failure, cooldown and concurrent probe tests.

## Slice 2: Gateway/Embedding Integration

- [x] Integrate route selection at the Worker composition root and preserve strict output
  schemas, citation validation and approval policy.
- [x] Add provider health and embedding dimension checks.

## Slice 3: Benchmark And Evidence

- [x] Add local deterministic/HTTP benchmark runner and report hashes/latency/errors/
  fallback/citation metadata, including an explicit fallback-count and breaker-state
  contract gate.
- [x] Add a GPU/vLLM manual gate rather than inventing throughput or memory values.
