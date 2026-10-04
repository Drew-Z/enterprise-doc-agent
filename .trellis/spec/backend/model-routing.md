# Model And Embedding Routing

## Optional Streamed Model Responses

### 1. Scope / Trigger

Agent and Presales may receive OpenAI-compatible SSE while preserving complete-result
validation, durable dispatch accounting, cancellation and existing total deadlines.
Streaming is transport support; it does not establish semantic quality or prove that
a particular proxy permits requests longer than its read timeout.

### 2. Signatures

- `OpenAIResponseReader(*, streaming: bool, max_bytes: int)` is created per request.
- `await reader.read(response: httpx.Response) -> httpx.Response` reconstructs a
  complete JSON envelope; `accounting_response` retains only observed metadata.
- `recorded_post(..., response_reader: OpenAIResponseReader | None = None)` records
  the dispatch before entering HTTP and preserves observed accounting on failure.
- Staging CLI accepts `--model-streaming` and `--fallback-model-streaming`.

### 3. Contracts

- `MODEL__STREAMING` and `MODEL__FALLBACK_STREAMING` default to false, independently.
  Deployment inputs accept only omitted/empty/`true`/`false`; false removes stale keys.
  Release fingerprints, guarded apply and rollback bind both keys. Workflow inputs
  are `STAGING_MODEL_STREAMING` and `STAGING_MODEL_FALLBACK_STREAMING`.
- Enabled requests send `stream: true` and `stream_options: {include_usage: true}`.
  Choice zero must finish with `stop` followed by `[DONE]`. UTF-8 and CR/LF boundaries
  may span transport chunks. Tool calls and changing response identities are rejected.
- The byte limit counts the decoded HTTP stream, including SSE and reasoning fields;
  heartbeat events never reset the enclosing route or persisted row deadline.
- Only complete assembled content enters the existing schema/citation validation.
  Unknown usage and monetary cost remain unknown. Accounting excludes answer/reasoning.
- Background Presales cancellation persists observed usage and IDs under the lease,
  retains the unresolved `running` call and rethrows cancellation. It does not confirm
  cancellation at the provider. Outer row timeout retains usage and records timeout.

### 4. Validation & Error Matrix

| Condition | Outcome |
| --- | --- |
| Complete stop and DONE | Original business validation before publication |
| Truncation, malformed SSE, identity drift, tool delta, upstream SSE error | Contract rejection; no partial publication or implicit retry |
| Excessive stream bytes | Response-too-large rejection |
| HTTP/transport timeout or network error | Existing retryable route policy with observed usage retained |
| External cancellation | Close stream, preserve bounded accounting, rethrow cancellation |
| No usage frame | Unknown usage, never inferred zero |

### 5. Good/Base/Bad Cases

- Good: primary non-streaming, fallback streaming, each with its own effort/timeout.
- Base: omitted flags preserve existing non-streaming behavior.
- Bad: accepting partial JSON after connection close or treating a heartbeat as a new budget.

### 6. Tests Required

- `test_model_stream_response.py`: chunk framing, byte bounds, malformed streams,
  final business checks, independent fallback settings, closure, deadlines and usage.
- `test_stream_provider_dispatch_integration.py`: one durable dispatch, budget denial
  before the next HTTP call, known/unknown usage under truncation/cancellation/network loss.
- `test_stream_background_integration.py`: cancellation/row timeout keep observed usage,
  preserve recovery state and publish no draft.
- `test_model_stream_configuration.py`: reject invalid configuration before writing,
  apply/restore both flags and detect fingerprint drift.
- `test_stream_presales_evaluation.py`: reconstructed envelope retains usage and supports
  offline scoring. Evaluator buffering is not evidence of live time to first token.

### 7. Wrong vs Correct

Wrong: return the accumulated answer at EOF and record missing token counters as zero.
Correct: require `stop` plus `[DONE]`, pass the envelope through the original validator,
and preserve unavailable token counters as unknown even when a request was charged.

## Adopted Facts

- Presales model calls allow up to 300 seconds and rows up to 900 seconds, with
  unchanged defaults. An optional fallback_model_timeout_seconds overrides only
  the fallback route; both explicit model overrides must remain below the row budget.
- A first background Presales dispatch reserves the smaller of half the remaining
  time and an available distinct fallback's advertised request timeout. Unknown
  custom-gateway bounds retain the half-budget reservation. The persisted row
  deadline, lease fence, two-dispatch cap and unknown-usage handling remain enforced.
- Deployment rendering and release guards bind Presales, model-route and Agent
  execution budgets. The release guard requires no active or queued work before
  configuration changes; it does not reset an in-flight task's deadline.
- ModelSettings exposes independent optional reasoning_effort and fallback_reasoning_effort
  values (low, medium, high, xhigh). Agent and Presales requests omit the field unless
  configured; fallback never inherits primary effort, and bounded Agent schema repairs
  keep the selected route's effort. This does not automatically select a task quality tier.
- Staging rendering and guarded release switching bind both settings to the existing
  configuration fingerprint and restore the original values or absence on rollback.
  The generation-only evaluator records configuredReasoningEffort for comparisons.
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

- Settings, model gateway, Presales citation selection, Worker composition, staging
  rendering, release switching and evaluator tests cover explicit effort and omission,
  invalid-value rejection, request preservation, fingerprint mismatch and rollback.
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
