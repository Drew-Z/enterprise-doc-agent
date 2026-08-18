# Model And Embedding Routing

## Adopted Facts

- `RoutedChatModelGateway` uses fallback only for retryable gateway errors.
- Exhausted bounded provider-output schema repair is retryable; permanent auth,
  provider-envelope contract, authorization and grounding failures do not silently
  fall back.
- Prompt v8/v9 may replace both identifiers of an invalid citation only when its stripped
  excerpt is a verbatim substring of exactly one supplied authorized evidence item. Zero
  or multiple matches remain unchanged for the deterministic grounding gate to reject.
- `CircuitBreaker` implements CLOSED, OPEN and HALF_OPEN with one in-flight probe.
- Primary and fallback calls share one optional monotonic route deadline. Fallback uses
  only remaining budget, and caller cancellation remains distinct from timeout.
- Route results and failures merge primary/fallback request, usage, optional token, repair,
  fallback and breaker telemetry. Provider-returned model identity is preserved verbatim;
  configured descriptor identity is used only when no observed identity is available.
- Route metadata records provider/model/revision/quantization/context and embedding
  dimension without secrets.
- `DimensionCheckedEmbeddingProvider` rejects item-count and vector-dimension mismatch.
- `scripts/benchmark_m7.py` is a deterministic routing benchmark, not GPU/vLLM evidence.
- `scripts/run_model_capacity.py` measures OpenAI-compatible streamed TTFT/TPOT from
  exact usage tokens and binds results to model revision, quantization, vLLM metrics,
  Prometheus GPU/KV/queue samples, `nvidia-smi`, environment and image digest.

## Proven Examples

- `packages/core/tests/test_model_routing.py` proves retryable-only fallback, permanent
  failure propagation, schema-failure telemetry merging, raw observed identity retention,
  shared deadline enforcement, cancellation propagation, single-probe HALF_OPEN behavior
  and embedding dimension rejection.
- `packages/core/tests/test_model_gateway.py` proves bounded output repair telemetry and
  unique-verbatim citation identifier recovery while ambiguous matches fail closed.
- `scripts/benchmark_m7.py` runs the versioned routing dataset repeatedly and records
  route metadata, outcome counts and latency summaries without claiming model quality.
- `tests/deployment/test_run_model_capacity.py` proves missing streamed usage cannot
  become a successful TPOT sample and validates a complete external report contract
  using synthetic telemetry only.
- `evidence/m7/20260719-m7-fallback-contract.json` records the deterministic fallback
  contract; real provider, GPU and vLLM performance remain external gates.

## Proven Files

- `packages/core/src/enterprise_doc_core/agents/gateway.py`
- `packages/core/src/enterprise_doc_core/documents/embedding_routing.py`
- `packages/core/tests/test_model_routing.py`
- `evaluation/m7_model_benchmark_v1.json`
- `scripts/run_model_capacity.py`
- `infra/capacity/model-capacity.example.yaml`
