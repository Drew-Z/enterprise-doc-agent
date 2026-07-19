# Model And Embedding Routing

## Adopted Facts

- `RoutedChatModelGateway` uses fallback only for retryable gateway errors.
- Permanent auth, contract and schema failures are returned without silent fallback.
- `CircuitBreaker` implements CLOSED, OPEN and HALF_OPEN with one in-flight probe.
- Primary and fallback calls share one optional monotonic route deadline. Fallback uses
  only remaining budget, and caller cancellation remains distinct from timeout.
- Route metadata records provider/model/revision/quantization/context and embedding
  dimension without secrets.
- `DimensionCheckedEmbeddingProvider` rejects item-count and vector-dimension mismatch.
- `scripts/benchmark_m7.py` is a deterministic routing benchmark, not GPU/vLLM evidence.

## Proven Examples

- `packages/core/tests/test_model_routing.py` proves retryable-only fallback, permanent
  failure propagation, shared deadline enforcement, cancellation propagation,
  single-probe HALF_OPEN behavior and embedding dimension rejection.
- `scripts/benchmark_m7.py` runs the versioned routing dataset repeatedly and records
  route metadata, outcome counts and latency summaries without claiming model quality.
- `evidence/m7/20260719-m7-fallback-contract.json` records the deterministic fallback
  contract; real provider, GPU and vLLM performance remain external gates.

## Proven Files

- `packages/core/src/enterprise_doc_core/agents/gateway.py`
- `packages/core/src/enterprise_doc_core/documents/embedding_routing.py`
- `packages/core/tests/test_model_routing.py`
- `evaluation/m7_model_benchmark_v1.json`
