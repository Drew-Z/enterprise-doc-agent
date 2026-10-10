# Question context, retrieval and failover audit — 2026-10-11

The authorized retrieval/decomposition repair is an opt-in local `presales.v25`
candidate. Default v24 and the last verified rc51 staging release are unchanged.
This continuation makes no real generation/embedding calls and performs no deployment.
The original public questions, failed attempts, reviews and trial materials remain.

## Current failover behavior

`OpenAICompatiblePresalesGateway.generate` makes one HTTP request with no internal
retry. When automatic background recovery is enabled, `freeze_policy` records two
routes and a two-dispatch ceiling. `_next_route` and `_dispatch` explicitly reject
reusing a route or endpoint/model route key within that attempt. Therefore an eligible
first primary failure directly uses the fallback slot; there is no primary retry.

| Outcome | Current background action |
| --- | --- |
| HTTP408/429/5xx, transport error, timeout, retryable upstream error | Try the other available route once, within deadline/budget |
| HTTP426 upgrade required | Cool that route for at least300 seconds; try the other route |
| Dispatched `presales_invalid_model_output` | Try the other route once; do not label the output contract rejection a transport outage |
| HTTP400/401/403, invalid citation, oversized input/output, authorization/delivery rejection | Stop; no blanket fallback |
| Both slots used, overall deadline, cancellation or no available route | Stop; retain failed/unknown accounting |

Per-row recovery differs from the circuit breaker: ordinary retryable failures
increment route health, with default threshold3/cooldown30 seconds; a half-open probe
can reopen immediately, and426 has its separate longer cooldown. Contract rejection
does not itself increment the transport-failure counter. A subsequent row generally
starts at the configured primary unless that route is cooling.

The retained `dc_certifications` attempt35f52e37-9c64-464d-adbd-9b4a2cd5a718 confirms
the actual sequence: primary `stream_contract`, then fallback `draft_contract`, two
dispatches and terminal failure. Primary usage remains unknown. Its frozen policy
binds wuye/gpt-5.6-sol, low, v24,120s and Grok4.7, medium, v15,180s; row budget360s.
These are the prior run's receipts, not a new live-state poll in this continuation.

The policy is deliberately cost/latency bounded, but coarse for transient failures.
Changing to another model and protocol after one failure can alter answer behavior;
a single error does not establish that the primary model or entire channel is bad.
A future policy should distinguish short transient failures from persistent channel
errors and model-output failures. Consider one delayed primary retry for eligible
transient failures, honoring Retry-After and the overall deadline, then an explicitly
qualified alternative route. If primary-retry-plus-fallback is required, it needs
three accounted slots or an explicit decision to spend the existing second slot on
the retry. Do not hide a retry inside HTTP transport or leave cost unknown/unrecorded.
No such retry/budget/model change is made here.

## Candidate change

V25 preserves the complete row as one immutable answer unit, including background,
examples, choices and all actual subquestions. Its instruction distinguishes context
from requested facts, and does not turn policy descriptions into extra prerequisites
or central management into a single-platform requirement. The strict output schema,
literal citations and existing response-size/language/business validators remain.
V21/v24 prompts and IDs remain reproducible; frozen cross-version policies reject.

For eligible v25 generation, each source still contributes at most two1800-character
chunks. Keep the first hybrid hit and use the second slot to add query terms absent
from the first, choosing only within that source's already accepted top10. Repetition
does not inflate coverage; ties retain the original rank. Authorization precedes
selection. No new keyword/vector query, model call or broader evidence budget is added.

`tests/presales/question_context_regressions.json` retains the original scope/password
questions and44,559 bytes of their attributed source bundles. They are known regression
data. Real PostgreSQL tests use controlled vectors favoring historically cited locations;
they do not reconstruct the original live embeddings or claim an unbiased quality rate.
The actual SQL/RRF/product path reproduces the v24 omission and delivers the exact
ISMS scope and minimum8/maximum128 password paragraph under v25, in both execution modes.

## Validation and remaining boundary

- 68 PostgreSQL integration cases passed, including eight v24/v25 known-regression
  cases and existing failover, deadline, authorization, quota and idempotency behavior.
- Gateway/protocol and collector/scorer tests preserve old identities, literal input
  binding and original failures. All provider boundaries are controlled in these tests.
- Ruff check and762-file format checks passed; mypy passed280 production files.
- 3,406 nonintegration cases and23 subtests passed (827 integration cases deselected)
  in268.65 seconds. The full source fingerprint is bound in the central verification receipt.

These checks prove evidence reachability and input/protocol behavior. They do not
prove that a live model now answers every aspect correctly or invents no obligations.
Lexical selection cannot recover evidence absent from top10, guarantee semantic
relevance, or preserve every conflicting clause in two slots. The existing2000-character
single-response limit also remains. A separately frozen, finite real-model regression
is the next qualification step before any release or change of default.

Recovery reuses the commercial task group at
`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness`,
phase `question_context_retrieval_20261011`, baseline329a607b3abfdb4dfcff5f874a7705d3c90c30cf.
Clean tracked files are recoverable from that commit; new paths are recorded absent.
Temporary PostgreSQL schemas are owned and removed by the fixture's finally block.
No historical source/evidence/backup or unrelated workspace work is deleted.
All96 prior trial artifact hashes and2,993 unrelated Git-status entries were verified
unchanged. No ad-hoc temporary workspace directory was created; fixture-owned database
schemas were dropped and their absence checked in fixture teardown. Existing test-runner
temporary output whose ownership is not individually recorded is left to its framework;
no broad temporary-directory cleanup is performed.
