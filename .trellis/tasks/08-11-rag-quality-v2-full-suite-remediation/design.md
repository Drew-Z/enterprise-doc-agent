# Design: RAG Quality V2 Full-Suite Remediation

## Design Principle

The first deliverable is observability, not a quality-score adjustment. One public error code can
remain stable while a separate allowlisted diagnostic identifies the failing boundary. Successful
citations receive stable-anchor summaries in the evaluator. Only after a targeted real run
provides this evidence may evaluator, prompt, MCP, or retrieval behavior change.

## Ownership Boundaries

- `enterprise_doc_core.evaluation.rag_quality` owns deterministic fact matching, stable-anchor
  mapping, per-citation diagnostic summaries, case scoring, and aggregates.
- `enterprise_doc_core.documents.retrieval` owns citation authorization and its internal
  subreason taxonomy. It does not decide evaluator gold labels.
- `enterprise_doc_core.jobs` owns durable job-attempt diagnostic persistence and the additive
  database migration.
- `enterprise_doc_core.agents.service` and the API Agent router expose authenticated attempt
  diagnostics. They expose codes only, never stored error messages.
- Worker `mcp_client` owns MCP operation/subcode classification. Worker queue settlement carries
  an already-sanitized diagnostic to the Job runtime.
- The staging evaluator owns report-safe projection and evidence orchestration. It may not relax
  runtime validation or retrieve database internals directly.

## Diagnostic Contract

### Durable attempt diagnostic

Add nullable `diagnostic_code VARCHAR(100)` to `job_attempts` through a new additive Alembic
migration. Existing rows and callers remain valid with null.

`JobHandlerError` gains a nullable `diagnostic_code` value constrained by code construction rather
than arbitrary exception text. `JobRuntimeService.fail` receives and persists the code on the
current fenced attempt. Queue settlement passes only this property; it never derives a diagnostic
from `str(error)`.

`AgentRunAttemptResult` and `AgentRunAttemptResponse` add nullable `diagnostic_code` /
`diagnosticCode`. The field is authenticated under the existing run-status endpoint. It is not a
Prometheus label or public event payload.

Allowed families:

- `grounding.citation_chunk_not_in_candidates`
- `grounding.citation_excerpt_empty`
- `grounding.citation_excerpt_too_long`
- `grounding.citation_excerpt_not_verbatim`
- `grounding.citation_not_authorized`
- `grounding.citation_wrong_version`
- `mcp.<known_tool>.<allowlisted_subcode>`
- `mcp.<known_tool>.returned_error`

Codes are bounded ASCII identifiers. Unknown tool names or unrecognized payload strings use a
generic allowlisted value; raw data is discarded.

### Grounding classification

Replace `validate_citations` string-only `ValueError` internals with a typed citation validation
error carrying:

- the existing public `RefusalReason` code;
- one bounded diagnostic code;
- the existing generic human-safe message.

`validate_grounded_output` maps that typed error to `GroundingValidationError` while retaining
both the public code and diagnostic. `AgentExecutionHandler` copies only those typed attributes
into `AgentExecutionRuntimeError`; unknown exceptions retain the generic execution error and no
diagnostic.

The authorization order remains version, candidate membership, tenant/version authorization,
then excerpt validation. No failing citation is rewritten during this slice.

### MCP classification

`McpStdioClient.call` passes the known `tool_name` into result parsing. Result parsing scans only
for an explicit allowlist of stable MCP error codes. Existing retryable-code behavior remains
unchanged. A rejected call returns:

- public code `mcp_tool_returned_error` or `mcp_tool_retryable_error`;
- diagnostic `mcp.<known_tool>.<allowlisted_subcode>` when recognized;
- otherwise `mcp.<known_tool>.returned_error`.

Tests use `ClientSession.call_tool` results with text and structured payload shapes. They prove
that a secret or arbitrary sentence never reaches the code.

### Evaluator citation diagnostics

Introduce an immutable evaluator diagnostic value per observed citation containing only:

- citation ordinal;
- resolved stable-anchor IDs;
- whether any stable anchor resolved.

`RagQualityCaseScore` also derives unexpected anchor IDs relative to the case gold set and an
unresolved citation count. The sealed case report adds these fields plus the last non-null
allowlisted attempt diagnostic from Agent status. It does not include chunk IDs, UUIDs, page text,
excerpts, answer text, error messages, or provider payloads.

The existing scoring equations and `passed` semantics do not change in the diagnostic slice.

## Diagnosis Run

Deploy diagnostics without changing prompt, dataset, retrieval, or citation authorization.
Execute the seven selected cases in deterministic order:

1. `fact-proc-quotes`
2. `fact-employee-remote-days`
3. `hard-travel-eight-hours`
4. `hard-support-response-updates`
5. `safety-contract-payment-note`
6. `safety-travel-first-class`
7. `safety-retention-delete-note`

Seal the report. Review the synthetic `fact-proc-quotes` answer through the existing authenticated
artifact UI/download path, but do not paste it into the report, task logs, or public events.
Record only a human conclusion and, if valid, the minimal accepted phrase in task research.

For a citation failure, the durable attempt diagnostic selects the branch. For a successful but
unresolved citation, the stable-anchor summary shows whether the citation maps to a different
known anchor. If no anchor maps, a one-time human review of the synthetic citation determines
whether the evaluator or citation selection is wrong.

## Remediation Branches

### Dataset branch

If the quote-count answer is semantically complete but uses an unlisted phrase, copy v2 to a new
dataset file and change only the dataset version plus the reviewed accepted variant. Validate the
same eight documents, 40 cases, category counts, corpus hash, and a new dataset hash. Keep v1/v2
loadable and byte-identical.

The evaluator matcher remains literal, normalized, and boundary-safe. This avoids turning an
evaluation label correction into fuzzy semantic grading.

### Anchor-resolution branch

If the payment citation is a verbatim controlling span that deterministically overlaps the gold
quote but fails full-string containment, add the smallest deterministic evaluator-only mapping
rule. The preferred shape is a reviewed normalized token-span/offset rule with a conservative
minimum overlap and page/document guard, proven against unrelated adjacent clauses. Do not use an
embedding model, model judge, heading-only match, or online authorization relaxation.

If the citation resolves to another chunk or unrelated anchor, do not change the evaluator; route
to prompt/citation remediation.

### Prompt and citation branch

The first change is a new prompt version that states:

- copy citation identifiers only from supplied evidence;
- copy each excerpt verbatim;
- cite the minimum sufficient evidence for the requested facts;
- include multiple citations only when separate facts require separate chunks;
- do not cite adjacent alternatives merely to explain contrast.

If a targeted repeat still produces valid-candidate/non-verbatim excerpts, consider one bounded
citation-only repair call. It receives the same frozen evidence, original structured output, and
an allowlisted issue code. It may change only citations, must keep answer/task/structured fields
identical, and must pass the same authorization gate. It may not repair unknown candidate IDs into
different evidence. This changes graph behavior and therefore requires an explicit graph version
bump and tests for one repair only.

After prompt v7 targeted runs `20260813-17` through `-25`, closed-label failures remain dominated
by direct-QA answers that contain the accepted controlling fact and expected authorized citation
but also repeat conflict prose. Prompt v8's known-chunk version normalization does not affect this
failure class. Prompt v9 therefore adds a deterministic answer projection after citation
normalization and citation-only repair:

- The payload must be a non-refusal `question_answer` with at least one citation.
- Every final citation pair must exist in the frozen supplied evidence, each excerpt must be a
  non-empty verbatim span of at most 500 characters, and chunk IDs must be unique. If any condition
  fails, retain the provider answer so the existing grounding gate remains authoritative.
- Replace only `answer_text` with the cited excerpts in citation order. Deduplicate identical
  excerpts without reordering; preserve citations and all other output fields.
- If an excerpt explicitly labels balanced double-quoted content as untrusted, omit only that
  quoted span while preserving the surrounding evidence statement. Do not perform general phrase,
  benchmark, or semantic filtering.
- Do not project summaries, structured extraction, refusals, empty citations, unknown pairs,
  non-verbatim excerpts, or duplicate chunks.

This keeps fact selection with the model's authorized citations while preventing uncited provider
prose from changing the direct answer. It does not relax authorization, repair a different
candidate, or import evaluator accepted/forbidden variants into production. The graph and provider
call count do not change, so only the prompt behavior version advances to `m4.v9`.

### Retrieval branch

Retrieval changes are last. For each case diagnosed as missing expected evidence, freeze keyword
and vector candidate IDs as synthetic test fixtures and assert channel rank, RRF ordering, final
top-k, and acceptance through `RetrievalService.retrieve`. Mock only the embedding provider and
recall repositories.

Change query behavior or fusion settings only if the expected anchor is missing in more than one
supported case for the same reason. Preserve authorization, deterministic tie-breaking, and
unsupported-question refusal separation. A per-case top-k exception is not allowed.

### MCP branch

An allowlisted deterministic MCP rejection is fixed in the owning tool and covered at both the MCP
adapter and owning service boundary. A retryable or isolated infrastructure failure remains in
evidence; existing job retry budgets handle it. No evaluator label changes are made for MCP
failures.

## Compatibility And Versioning

- The migration is additive and nullable; old images ignore the new column, and new images accept
  old null rows.
- The Agent status response adds an optional field. Existing consumers that ignore unknown fields
  remain compatible; API contract tests lock the camelCase shape.
- Public top-level error codes remain stable. Diagnostic codes are additive and explicitly not a
  retry classifier by themselves.
- Evaluator report shape changes require tests and a report/evaluator version marker so reports
  with different diagnostic semantics are distinguishable.
- Dataset gold changes create v3 rather than editing v2.
- Prompt changes increment only the prompt version unless graph behavior changes. Citation repair
  increments graph and prompt behavior versions. MCP result schemas remain unchanged unless a
  tool's wire contract genuinely changes.
- Prompt v9 direct-QA projection is local post-processing with no new node or provider call, so it
  increments only the prompt behavior version. Prompt v8 remains selectable for rollback.

## Security And Privacy

- Diagnostic code constructors accept enums/known literals, not arbitrary strings.
- The API exposes no `error_message` or provider body.
- Reports continue hashing answers and queries and omit exact model output and citation excerpts.
- Unit tests seed tokens, URLs, UUIDs, document sentences, and malicious MCP text and assert that
  none appear in serialized status/report output.
- Diagnostics are not metrics labels because subcodes may expand over time and run-level values
  are too high-cardinality.

## Rollout

1. Add the diagnostic migration and code, run local gates, and deploy one immutable image.
2. Run and seal the seven-case diagnosis without any semantic remediation.
3. Complete human review and update the taxonomy research with confirmed branches.
4. Implement only confirmed branches using public red/green tests; bump behavior/dataset versions
   as required.
5. Deploy one final immutable remediation image.
6. Run the seven-case set until three consecutive complete passes.
7. Run the bounded 12-case suite once.
8. Run the full 40-case suite until three consecutive complete passes on the same image and
   dataset version. Preserve every failed or interrupted report.

## Rollback

- Application rollback uses the previous image digest and behavior-version settings.
- The nullable diagnostic column remains in place; no destructive down migration is required for
  application rollback.
- A prompt/graph rollback restores the previous behavior versions so in-flight runs are not
  resumed by an incompatible graph.
- New dataset versions and reports remain historical evidence even if runtime code rolls back.
- Any failed quality repeat remains immutable and resets the consecutive-pass sequence.
