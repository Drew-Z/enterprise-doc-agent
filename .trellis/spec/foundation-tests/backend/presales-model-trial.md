# Presales Model Trial Contract

## 1. Scope / Trigger

Task-local inference diagnostics at .trellis/tasks/09-11-saas-presales-model-trial/. The frozen input has 30 pre-segmented synthetic requirements. This runner does not invoke the production app, retrieval stack, OCR, customer review, billing, or SaaS access flows.

## 2. Signatures

model_trial.py exposes TrialConfig, read_codex_route(Path), read_chat_route(Path), and async run_trial(config=..., output_dir=..., run_id=..., input_path=..., transport=None).

The CLI accepts --config, --output-dir, --run-id, optional --input, --deadline (0 < value <= 300), --max-output-tokens (16..20000), and --reasoning-effort (low/medium/high). CLI configuration is the current Codex Responses route. Alternate Chat Completions calls use read_chat_route and run_trial; exact reproducible commands are saved in each experiment-plan JSON.

Exit 0 means a complete schema-valid prediction was saved; exit 3 means a request failed and run.json was preserved; exit 2 means invalid configuration/input/file operation. Scoring remains a separate invocation of the existing benchmark.py score.

## 3. Contracts

Input raw bytes must match the frozen SHA-256 c5874656688a3fbc30cb3d6d0aa3fd104d795112ad0f378f0ec89470966075be. Responses input or Chat user-message content is exactly that file. Only output metadata instructions are added. Gold, this conversation, previous model output, retrieval tools, and reference demos are excluded.

Responses: stream=true, store=false, bounded max_output_tokens, no previous_response_id. Chat: stream=true, stream_options.include_usage=true, max_tokens limit, temperature=0, response_format=json_object, tools=[], tool_choice=none. These are client-request properties; hidden provider behavior is not independently attested.

Only a completed Responses event or Chat [DONE] plus finish_reason=stop can create predictions.json. The model's JSON text is validated against the existing PredictionRun contract and preserved unchanged; no code-fence repair, label repair, citation repair, or answer rewriting occurs. Unknown/duplicate requirement identifiers are rejected by the subsequent score command.

Output directory must not exist. Each run saves request.json, bounded response.sse, run.json, and when available provider-response.json/model-output.txt/predictions.json. For Chat, provider-response.json is explicitly an assembly of streamed text and reported usage; response.sse is the original protocol evidence. Failed requests have no fabricated prediction.

run.json records input/request/response hashes, requested/returned model, response ID, elapsed time, limits, HTTP status, usage or null, cost_amount=null when price/bill is unknown, error code, retry count, and configured versus injected transport. Retained buffer hashes are partial when the byte limit is exceeded; consult received_bytes, retained_bytes and response_capture_complete. No percentile or per-row latency is inferred from one batch.

Model credentials are read in memory from the exact selected config. Existing Windows loopback HTTP(S) system proxy may be explicitly applied to the external Chat route; globals are unchanged. trust_env=false alone does not consume Windows registry proxy settings. Never route a local provider through an external proxy merely to work around concurrency limits.

Provider usage fields are observations, not interchangeable billing units. Preserve prompt/completion totals, reasoning/cached details and provider-specific context_details separately. A reported completion total above requested max_tokens does not establish an enforced budget ceiling; cost_in_usd_ticks=0 does not establish a zero bill. Keep currency cost null until the route's price, metering semantics and bill are verified.

Store semantic review separately from raw predictions, bound by predictions_sha256 and corpus_sha256. Record reviewer_kind, blind_review and independent_domain_adjudication explicitly. An assistant review may suggest wording changes but must not rewrite the measured output, change the original unreviewed state, or imply business approval. A mechanical score replay must continue to match its original record.

## 4. Validation & Error Matrix

| Condition | Observable result |
| --- | --- |
| Changed input / oversized input | Fail before network or directory creation |
| Existing output directory | output_directory_exists; files untouched |
| Capacity, concurrency, missing model, HTTP error | One attempt and preserved failed run; no quality score |
| Timeout / missing terminal / length cutoff | Failed run, bounded raw evidence, no predictions |
| Credential echoed by provider | Redact before save, retain raw-buffer hash, mark failure |
| Invalid prediction JSON / metadata | Save original model text; no repair or fabricated successful run |
| Missing usage | null, never zero; money cost remains unknown |
| Model listed by GET /models | Discovery only; actual inference may still reject it |

## 5. Good / Base / Bad Cases

Good: run the frozen input, save actual output, then apply the unchanged scorer and separately inspect draft meaning. Base: a synthetic fixture tests the runner with injected HTTP transport but is never a live model observation. Bad: classify a capacity rejection as zero model accuracy or switch model names while attributing results to the original model.

## 6. Tests Required

Run test_presales_model_trial.py. HTTP MockTransport is the inference boundary; a mocked OS proxy lookup covers configuration. Verify exact input isolation, raw output/usage retention, incomplete responses, HTTP failures, timeout and byte cap, file protection, secret redaction, missing usage, Chat termination/tool-call rejection, and unchanged config bytes. Temporary directories and mypy caches must be automatically removed.

Lint/type-check current task Python only; old runner source snapshots are immutable execution evidence. New snapshots are retained whenever runtime behavior changes after an actual request. Final replay checks must bind each attempt to the runner version, input, raw protocol response and predictions used.

## 7. Wrong vs Correct

Wrong: use gold to fill absent rows after a failed request, interpret listed model IDs as verified availability, or present a synthetic result as customer ROI.

Correct: retain every failed attempt, use a distinct plan/run for a new model or corrected transport, preserve original answers and references, report semantic review as assistant review, and keep independent business review and paid customer validation open.
