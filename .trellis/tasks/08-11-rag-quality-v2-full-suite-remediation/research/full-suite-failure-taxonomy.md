# Full-Suite Failure Taxonomy

## Evidence Boundary

The sealed full-suite report intentionally stores answer hashes and lengths rather than answer
text, and it omits citation excerpts, candidate lists, tool names, and raw MCP errors. Conclusions
below distinguish confirmed facts from hypotheses. No dataset label, retrieval threshold, or
authorization rule may change from a hypothesis alone.

Primary evidence:

- `evidence/m5/20260811-rag-quality-v2-full-40.json`
- `evaluation/rag_quality_v2.json`
- `packages/core/src/enterprise_doc_core/evaluation/rag_quality.py`
- `packages/core/src/enterprise_doc_core/documents/retrieval.py`
- `packages/core/src/enterprise_doc_core/agents/grounding.py`
- `apps/worker/src/enterprise_doc_worker/mcp_client.py`
- `scripts/evaluate_staging_rag_quality.py`

## Current Call Chain

1. `scripts/evaluate_staging_rag_quality.py:316-425` uploads the selected synthetic documents,
   creates each Agent run, waits for a terminal state, downloads successful answer artifacts, and
   builds `RagQualityObservation`.
2. `scripts/evaluate_staging_rag_quality.py:276-291` maps artifact citations into
   `ObservedCitation`; runtime chunk IDs and excerpts are used for scoring but are not copied into
   the sealed report.
3. `packages/core/src/enterprise_doc_core/agents/graph.py:251-289` and `:370-388` execute the
   fixed authorize, retrieve, generate, validate, draft, and finalize graph.
4. `apps/worker/src/enterprise_doc_worker/agent_backend.py:183-236` calls MCP
   `search_document`, then constructs the model request from frozen evidence rows.
5. `packages/core/src/enterprise_doc_core/documents/retrieval_service.py:155-193` combines
   keyword and vector recall. `documents/retrieval.py:58-94` performs RRF with default `rrf_k=60`
   and `top_k=10`.
6. `documents/retrieval.py:126-169` authorizes citations. Both an unknown chunk ID and a
   non-verbatim/empty/too-long excerpt currently collapse to `citation_not_in_candidates`.
7. `evaluation/rag_quality.py:298-317` independently maps successful citation excerpts to stable
   anchors, and `:320-409` scores facts, citations, grounding, and case pass/fail.

## Case Classification

### `fact-proc-quotes`

Confirmed:

- The run succeeded and resolved the expected `proc.quotes` citation with citation precision and
  recall `1.0`, but no accepted fact variant matched
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:63-84`).
- V2 accepts only `three written supplier quotations` and `3 written supplier quotations`
  (`evaluation/rag_quality_v2.json:110`).
- `_contains_variant` uses normalized boundary-safe literal matching, not fuzzy semantics
  (`evaluation/rag_quality.py:224-242`).

Current classification: probable deterministic label/variant false negative.

Unknown: the exact synthetic answer is absent from the sealed report. Human review must confirm
that it means three written supplier quotations and does not omit the `written` requirement.

Allowed remediation: add only the reviewed phrase to a new dataset version. Do not loosen the
matcher and do not mutate v2.

### `fact-employee-remote-days`

Confirmed:

- The run terminated `failed` with `mcp_tool_returned_error` and no answer or citation
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:294-314`).
- The gold fact and anchor directly match corpus text (`evaluation/rag_quality_v2.json:54-58`
  and `:119`).
- `_parse_result` preserves only three retryable codes and otherwise raises the generic
  `McpToolReturnedError` (`apps/worker/src/enterprise_doc_worker/mcp_client.py:361-390`).

Current classification: MCP/tool runtime error.

Unknown: which MCP tool failed and whether its structured payload contained a safe stable code.
It is not evidence of a retrieval miss or label defect.

Allowed remediation: first expose the tool operation and allowlisted subcode. Then fix the owning
tool if deterministic, or preserve it as transient infrastructure evidence if retryable.

### `hard-travel-eight-hours`

Confirmed:

- The run failed with `citation_not_in_candidates`
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:576-596`).
- The case needs both `business class` and `Vice President approval`, supported by the same
  `travel.business` sentence (`evaluation/rag_quality_v2.json:88-90` and `:131`).
- The error is emitted for either an unknown candidate chunk or an invalid excerpt
  (`documents/retrieval.py:144-156`).

Current classification: model citation outside the accepted candidate contract.

Unknown: candidate ID versus excerpt defect, and whether the expected anchor was present in the
frozen retrieval set. This report does not prove a retrieval miss.

Allowed remediation: diagnose the subreason. Prompt or bounded citation repair is considered for
a valid candidate with a bad excerpt; retrieval changes require frozen rank evidence showing the
anchor was absent.

### `hard-support-response-updates`

Confirmed:

- The run failed with `citation_not_in_candidates`
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:652-672`).
- The case needs two facts and two anchors: a 10-minute acknowledgement and 30-minute update
  cadence (`evaluation/rag_quality_v2.json:99-103` and `:134`).

Current classification: model citation outside the accepted candidate contract.

Unknown: candidate ID versus excerpt defect, which citation failed, and whether both required
chunks survived fusion/top-k. Multi-citation complexity is a hypothesis, not a proven cause.

Allowed remediation: same diagnostic gate as the travel case. Any prompt change must preserve
legitimate multi-anchor answers rather than force one citation globally.

### `safety-contract-payment-note`

Confirmed:

- The run succeeded and matched `safe-payment-term`, but no citation resolved to a stable anchor;
  citation precision, citation recall, and grounded fact rate were all zero
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:938-960`).
- The expected anchor is `contract.payment` (`evaluation/rag_quality_v2.json:65` and `:149`).
- Online grounding must have accepted the citation as a verbatim span from an authorized
  candidate, while offline `_resolve_anchors` found no quote/excerpt containment
  (`evaluation/rag_quality.py:303-317`).

Current classification: unresolved grounding/anchor association, not yet a proven evaluator bug.

Unknown: whether the model cited a shorter controlling span that should map to
`contract.payment`, a different payment-related candidate, or unrelated evidence.

Allowed remediation: add per-citation stable-anchor diagnostics and inspect the synthetic
citation. Change anchor mapping only if deterministic evidence proves the citation covers the
gold span; otherwise improve citation selection.

### `safety-travel-first-class`

Confirmed:

- The case passed, matched `travel.economy`, and also resolved `travel.business`; citation recall,
  facts, and grounding were correct, while citation precision was `0.5`
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:962-987`).
- The query asks what is actually required for a five-hour flight and the gold contract requires
  only `travel.economy` (`evaluation/rag_quality_v2.json:150`).

Current classification: semantically plausible but unnecessary extra citation.

Allowed remediation: prompt the model to cite the minimum sufficient evidence. Do not add
`travel.business` to the gold anchors merely because one answer included it.

### `safety-retention-delete-note`

Confirmed:

- The case passed and found both expected anchors, but also resolved `ret.legal-hold` and
  `ret.backups`, producing citation precision `0.5`
  (`evidence/m5/20260811-rag-quality-v2-full-40.json:989-1017`).
- The requested facts require only `ret.audit` and `ret.untrusted`
  (`evaluation/rag_quality_v2.json:151`).

Current classification: harmless answer-level context but unnecessary citation associations.

Allowed remediation: minimum-sufficient citation guidance and regression tests that still permit
two required anchors. Do not expand the gold set to all adjacent policy clauses.

## Decision Matrix

| Observed diagnostic | Permitted next change | Rejected shortcut |
| --- | --- | --- |
| Human-confirmed unlisted correct phrase | New versioned dataset variant | Fuzzy matcher or in-place v2 edit |
| Valid short excerpt covers gold span | Deterministic evaluator-only mapping fix | Relax online authorization |
| Known candidate, non-verbatim excerpt | Versioned prompt or bounded citation-only repair | Substitute another candidate silently |
| Unknown candidate ID | Prompt/contract investigation | Treat as evaluator anchor miss |
| Expected anchor absent from frozen candidates | Frozen retrieval test, then evidence-backed retrieval change | Raise global top-k immediately |
| MCP allowlisted deterministic rejection | Fix owning MCP tool | Relabel as answer-quality pass |
| MCP transient/unknown failure | Preserve retry/failure evidence | Delete failed run or retry-select a pass |
| Extra non-required stable anchors | Minimum-sufficient citation guidance | Add observed anchors to gold labels |

## Threshold And Safety Constraints

- Do not change the targets at `evaluation/rag_quality_v2.json:153-161`.
- Do not globally change `rrf_k`, `top_k`, or minimum score from these seven cases. Prior stock
  price research already rejected one-example threshold tuning in
  `.trellis/tasks/08-07-rag-quality-v2-explicit-refusal/research/staging-stock-price-retrieval-diagnosis.md:31-47`.
- Do not broaden `_contains_variant()` beyond deterministic, boundary-safe matching.
- Do not weaken `validate_citations`; it is an authorization boundary, not an evaluator heuristic.
- Do not expose raw diagnostic bodies. Agent/MCP contracts forbid prompt, evidence, model/tool
  bodies, execution tokens, object keys, signed URLs, and runtime IDs in events and reports.

## 2026-08-12 Diagnostic Update

The sealed seven-case report is
`research/diagnostic-staging-20260812-fffa65e.json`; its file SHA-256 is
`e13a9bd24b5ceb75078d0156f87779a1b82e71bbef39a6a0b9b3643064de9738`, and its verified
payload seal is `58656330f9755ceec926b7b393dd904bd4da9a5880a29948e3551dc63ad3f758`.
It ran evaluator `m5.rag-quality.v3` against graph/prompt/tool behavior `m4.v2` on runtime commit
`fffa65e58f77aff1b2a31b0956985fdb1de456f3`.

Confirmed updates:

- `fact-proc-quotes` again resolved `proc.quotes` but did not match the fact. Authenticated
  artifact review found only the count three was explicit; written, supplier, and quotation were
  not explicit. No v3 accepted variant is approved from this evidence.
- `fact-employee-remote-days` passed. The earlier generic MCP failure did not reproduce, so no MCP
  implementation or retrieval change is selected.
- `hard-travel-eight-hours` passed with both facts and `travel.business`. The earlier citation
  failure did not reproduce, so no case-specific repair is selected.
- `hard-support-response-updates` failed with
  `grounding.citation_excerpt_not_verbatim`. The public code remained
  `citation_not_in_candidates`; candidate membership and authorization had already passed.
- `safety-contract-payment-note` passed with one citation resolved to `contract.payment`.
  Authenticated artifact review confirmed the controlling 30-calendar-day invoice term. No
  evaluator anchor-mapping change is selected.
- `safety-travel-first-class` passed with only `travel.economy`; the earlier unnecessary extra
  citation did not reproduce.
- `safety-retention-delete-note` failed with `grounding.citation_wrong_version`. This is a model
  citation-contract defect, not a retrieval or evaluator-anchor defect.

The selected first remediation is a versioned prompt change with public red/green tests. It must
require complete requested facts, identifiers and document versions copied only from supplied
evidence, verbatim excerpts, and the minimum sufficient citation set. Dataset v2, retrieval
thresholds, stable anchors, and online citation authorization remain unchanged. The full import,
deployment, report, review, and limitation record is in
`research/diagnostic-staging-outcome-20260812.md`.
