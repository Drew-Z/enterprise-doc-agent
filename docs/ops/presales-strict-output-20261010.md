# Optional strict provider schema, 2026-10-10

Presales can now explicitly request JSON Schema output from either configured
route. This fixes the missing request contract: previously the schema appeared only
in the system message while response_format requested generic JSON. The default
remains unchanged v15/JSON mode. No staging setting or deployment changed.

## Implementation

Set `PRESALES__PRIMARY_STRICT_OUTPUT=true` or
`PRESALES__FALLBACK_STRICT_OUTPUT=true` only for an endpoint selected for this mode.
Both default false. API and Worker must share the configuration; accepted old
prompt policies must drain before a rollout. For offline collection, the explicit
`--strict-output` flag selects only the collector's requested route.

The strict schema comes from a small Pydantic BasisDraft subclass that makes both
previously optional arrays required. All nested object fields are already required
and extra properties forbidden. The same generated schema is sent under
`response_format.json_schema` with strict=true and validated locally. Existing
literal citations, source identity, status, Chinese prose, size and prerequisite
rules remain in force. No type coercion, added provider retry or hidden same-route
downgrade was introduced. Existing configured two-route recovery is unchanged.

Strict mode has prompt version v18 and SHA
`6c65e4674e84d8a6a5639a08905ef270e48dce3a27a474927a0c7038995b4a29`.
The existing frozen execution policy binds that identity; restoration keeps the
selected mode, and mismatched templates are refused before sending. Legacy v15
has the same exact prompt SHA as before. No database, HTTP response or browser
schema change is required.

Strict reports use run-v8 and record responseFormat. Both scorers verify the
appropriate schema, and failed observations remain failed. Historical v1..v6
decoders and recorded scores are unchanged. The rejected run-v7 answer-aspect
candidate remains distinct and is not accepted as a new production contract.

## Endpoint observation and limits

One new synthetic audit-module fixture and separate assistant reference were
frozen before dispatch on the existing primary grok-4.7/low/streaming/120s route.
The endpoint accepted the strict request (HTTP200) and returned schema-conformant
JSON in37.563s. All required arrays and fields were present. This single observation
cannot prove the relay actually enforces constrained decoding or future reliability.

The draft still failed local business validation with support_combination. It
merged purchased, unconfigured and unrecorded-acceptance prerequisites into one
question-shaped proposition, populated positive and negative with uncertainty=none,
and left missingInformation empty. The raw prose does not justify promotion or a
claim of improved semantic quality. No second model request was made. Reported
usage:3,035 input tokens and1,796 completion tokens; verified monetary cost unknown.

Read-only pre/post observations found rc49/0037/v15, five ready workloads, no active
jobs and unchanged policy/application ledgers. Conservative UTC2026-10-09 budget:
36 application calls +17 known/reserved direct calls =53/200, including three
previous unknown calls. No embeddings, source/tenant writes or old public replay.

## Verification and recovery

Nine core tests and two collector/scorer tests failed before implementation, then
passed. The focused143-test suite also preserves historical scoring. Eighteen
real PostgreSQL tests cover synchronous/durable strict execution, idempotent replay,
one charge and both mode-drift directions with zero dispatch. Their fixture-owned
schemas were removed in finally. Full final check receipts are recorded centrally.

Recovery uses the existing commercial task group, phase
`strict_output_contract_20261010`, baseline `500185e4a6963cb9ff526f36616e86ff24058a77`.
Exact Git paths/hashes are registered before edits; new files are recorded absent.
The controller, plan, original failed response, review and read-only postflight are
retained as `strict-output-contract-*` under fallback-model-evidence. The executed
controller must not be run again. Historical failures and unrelated workspace
changes are preserved. This implementation adds an explicit format capability;
answer completeness, business-event decomposition and competitor acceptance remain open.

References consulted: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
distinguishes schema enforcement from JSON syntax and requires local handling of
incomplete/refused responses. [Provider structured-output documentation](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)
describes supported schema features and constraint limits; it does not establish
the deployed relay's behavior. Local business validation remains necessary.
