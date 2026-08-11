# Design: RAG Quality V2 and Explicit Model Refusal

## Boundaries

- `enterprise_doc_core.evaluation.rag_quality` owns deterministic normalization, variant
  matching, stable-anchor mapping, per-case scoring, and aggregates.
- `enterprise_doc_core.agents.schemas` owns the strict answer/refusal output union.
- `enterprise_doc_core.agents.gateway` owns provider prompting, parsing, and bounded repair.
- `enterprise_doc_core.agents.grounding` converts a contract-valid answer into a grounded
  answer or a contract-valid model refusal into `GroundedRefusal`.
- `enterprise_doc_core.agents.graph` routes refusal outcomes before any draft or approval node.
- Staging scripts orchestrate trials but may not weaken production validation behavior.

## Evaluator Changes

Anchor resolution returns zero or more anchor IDs for each citation. Scoring flattens those
associations for recall and grounding. Citation precision counts every mapped association as a
prediction and counts an unmapped citation as one incorrect prediction, preventing a citation
that spans two anchors from producing precision above one.

Variant matching operates on NFKC/case-folded text and searches each normalized variant with
Unicode-aware alphanumeric boundaries. Punctuation and whitespace remain normalized enough for
deterministic matching, while a numeric variant such as `15` cannot match `150`.

The v2 dataset reuses the unchanged corpus directory. It changes only the dataset version and
reviewed accepted variants. The v1 JSON and v1 report hashes remain untouched.

## Model Outcome Contract

Answer payloads carry `outcome: "answer"` and keep the existing task-specific fields. Refusal
payloads carry `outcome: "refusal"`, the requested `task_type`, and
`refusal_reason: "insufficient_evidence"`; all answer/citation/risk fields are JSON null or an
empty citation array as prescribed by the strict schema.

Only the model's inability to support an answer from supplied evidence is self-declarable.
Authorization, wrong-version, missing-citation, duplicate-citation, and provider contract errors
remain deterministic failures. The prompt explicitly tells the model to choose refusal rather
than fabricate or emit an uncited answer.

## Graph And Compatibility

`validate_grounded_output` returns `GroundedAnswer | GroundedRefusal`. The graph validation node
stores an answer fingerprint only for answers. For a valid refusal it stores `outcome=refused`
and the reason, then a conditional edge routes directly to `finalize_refused`.

The graph, prompt, and tool/output schema behavior versions move from `m4.v1` to `m4.v2` for new
runs. Existing completed runs remain readable. In-flight old-version runs are not resumed by the
new graph implementation; rollback restores the previous image and defaults.

## Retrieval Diagnosis

The failed stock-price run is inspected through sanitized staging database evidence: rank,
rounded RRF score, and stable corpus anchor mapping only. Global retrieval thresholds change only
if broader unsupported-case evidence demonstrates a consistent separation from supported cases.
The explicit model refusal path is required even if retrieval thresholds are later improved.

## Rollout And Evidence

1. Deploy the versioned contract and run a health check.
2. Execute the four prior failures three times each using v2.
3. If all targeted runs pass, execute the bounded 12-case suite.
4. If bounded targets pass, execute all 40 cases and repeat as the cost/time budget permits.
5. Seal each new report under a v2-specific path and leave v1 evidence unchanged.

## MCP Timeout Recovery

The third targeted staging repeat exposed a Worker-side MCP timeout while
`search_document` was still retrieving. Cancellation must mark the current tool execution as
interrupted so the same idempotency key can retry immediately instead of waiting for the normal
stale threshold. `ToolExecution.started_at` is also the retrieval lease version: stale takeover
advances it, and freeze/fail/deny/cancellation writes compare the expected value under row lock.
An older retrieval may finish after takeover, but it cannot freeze evidence or change the new
attempt's terminal state.

If a stale execution has no frozen summary or evidence, the new lease reruns retrieval. If a
completed summary exists, recovery replays/finalizes it without another retrieval. The Worker
classifies known retryable MCP codes from text or structured protocol error payloads so transport
serialization differences cannot turn `tool_execution_in_progress` into a permanent job failure.

## Provider Failure Retry Budget

Targeted repeats four, five, and seven passed, while repeats six and eight failed only on
`hard-support-objectives` with `model_server_error`. The same case passed in the surrounding
runs, so those reports are retained as transient-provider evidence rather than relabeled as
answer-quality failures.

`AgentSettings.execution_max_attempts` owns a bounded job-level retry budget. Its default remains
three and the single-node staging profile sets five. `AgentRunService` applies the setting to the
initial execution job and the API passes the same value to `ApprovalService` for resumed jobs.
The existing job retry/backoff contract remains responsible for scheduling attempts; increasing
the budget does not convert an exhausted provider failure into success or hide its terminal code.

Rollback uses the previous container digest and behavior version. No database schema migration is
required. New v2 evaluation files can remain as historical inputs even if runtime code rolls back.
