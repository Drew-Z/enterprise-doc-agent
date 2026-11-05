# Grok / GPT fixed semantic comparison — 2026-10-10

The newly authorized second provider channel successfully serves requested and
returned `gpt-6-luna`. All three GPT observations produce valid candidate drafts
and preserve the central unknown-state distinctions. Material-request wording and
the explicit current-enablement decision still warrant human refinement.

Grok remains the user's primary target. Its baseline and one bounded prompt
correction do not pass this regression review. No candidate, prompt or model is
promoted to staging. This records an unsuccessful correction, not completed Grok
semantic acceptance or a model-wide capability claim.

## Fixed cases and controls

The same known CQU02 performance, CQU05 personnel and synthetic QP01 mixed-state
inputs are used for both channels. CQU inputs contain historical buyer requirements
only; there are no supplier test results or personnel records. QP01 contains met,
unmet, unknown/missing, unknown/conflict and another-order facts. These are known
regressions, not unseen evaluation, real users or competitor measurements.

The prospective plan separates substantive mistakes from clarification detail.
Fabricated facts, rewritten qualifications, wrong states and inconsistent actions
are substantive. Missing explicit resource configuration in a test-condition request
is an improvement item. Old outputs and their original frozen verdicts are unchanged.

Both channels use the existing private question candidate with complete source
context, the same system/schema, low effort, streaming, a 120-second deadline and
4,000 requested tokens. GPT uses `max_completion_tokens` and omits empty tool fields;
Grok retains `max_tokens`. Effective requests are recorded without credentials.
Source content and question identity are verified per request; opaque citation
nonces differ. Endpoints and credentials also differ, so this is a comparison of
channel/model configurations, not a controlled comparison of model weights.

The second channel uses the local `FALLBACK_*2` fields, explicitly verifying
`FALLBACK_PROVIDER_NAME2=gpt`, `FALLBACK_MODEL_NAME2=gpt-6-luna` and
`FALLBACK_PROTOCOL2=chat_completions`. The production route loader is unchanged.
The fetched [official model page](https://developers.openai.com/api/docs/models/gpt-6-luna)
documents the requested model, Chat Completions and structured outputs. Actual
third-party availability is evidenced by these responses, not inferred from that page.

## Original observations

| Phase / channel | Case | Original state | Seconds | Reported tokens | Separate review |
| --- | --- | --- | ---: | ---: | --- |
| Baseline Grok | CQU02 | failed / stream_contract | 102.921 | unknown | No decoded answer; semantics unassessable |
| Baseline GPT | CQU02 | succeeded | 35.438 | 4,622 | Core requirements and missing evidence preserved |
| Baseline Grok | CQU05 | succeeded | 50.172 | 8,645 | Buyer rules incorrectly treated as met/supported |
| Baseline GPT | CQU05 | succeeded | 45.313 | 6,083 | Correct uncertainty and specific experience in rule/answer; materials request needs precision |
| Baseline Grok | QP01 | succeeded | 65.547 | 8,540 | Missing verification result assessed unmet |
| Baseline GPT | QP01 | succeeded | 42.969 | 7,405 | All six states correct; make current enablement decision more explicit |
| Revised Grok | CQU02 | succeeded | 41.563 | 6,107 | Buyer requirements still assessed met despite insufficient_evidence |
| Revised Grok | CQU05 | succeeded | 35.656 | 7,216 | Rule existence still substituted for actual qualification |
| Revised Grok | QP01 | failed / draft_contract | 91.532 | 10,416 | Wrong rule indexes; raw text retains unknown-as-unmet |

The first Grok CQU02 received HTTP200 and 719,686 decoded wire bytes, but the stream
reader rejected its contract. The collector did not retain a decoded response.
The exact stream defect, answer content, usage and billing cannot be reconstructed
from this receipt. Do not call it a timeout or semantic failure.

### Grok: the central meaning error remains

Baseline CQU05 says the supplier provided no personnel proof, yet declares
`status=supported` and both prerequisite states `met`, citing buyer rules. It also
omits the specifically required software project management experience.

Baseline QP01 assesses an unentered verification result as `unmet` and requests
record entry instead of first establishing the actual result. Another response
incorrectly includes already-completed licence and training in the current gaps.
Correct conflict handling and other-order exclusion do not cure these errors.

The single correction has distinct identity
`presales.question-assessment-semantic-precision.v1`. It changes only the prompt:
assess actual business facts, distinguish rule existence from completion, use a
counterfactual test for absent documentation, preserve qualification categories
and keep conclusions/actions consistent. It adds no output fields or model stage.

Actual revised requests contain that exact prompt. Nevertheless, revised CQU05
explains `met` as:

> 存在原文要求项目经理持有PMP管理资质的证据

That is evidence of a rule, not evidence the supplier holds the qualification.
Revised QP01 uses rule indexes 1..6 for six zero-based rules 0..5; the unchanged
resolver reproduces `every rule requires exactly one assessment`. Its raw action
also says:

> 需录入台账以证明校验通过

The result does not establish that verification passed. Preserve the original
failed draft and the raw semantic mistake independently; do not shift indexes or
rewrite the answer to turn it into a success. One correction has not solved the
problem, and its few observations do not establish causal improvement or decline.

### GPT: useful comparative evidence with limits

GPT CQU02 correctly requests performance results and measurement conditions while
retaining uncertainty. Explicit resource configuration would improve the request.

GPT CQU05 retains general work experience and the more specific software project
management experience in its rule and answer, without making them alternatives.
However, its summary/action/materials list falls back to general relevant experience.
Before external delivery, repeat the specific category in the requested evidence.
This differs from the previous GLM output's explicit general-experience OR specific-
experience substitution; it is still a meaningful clarity limitation.

GPT QP01 gives all six expected states and first asks whether verification passed.
It adds no training-proof duty and preserves both conflicting records. The overall
wording says current enablement cannot be confirmed; it should more directly say
known incomplete configuration/signoff prevent enabling now. Neither these findings
nor three valid drafts establish independently approved semantic acceptance.

## Verification, accounting and decision

Six baseline HTTP controls and six revised-prompt controls pass: both channel
requests, success, HTTP503 and cancellation retain one dispatch, redaction and
adapter restoration. Offline inspection binds all nine complete inputs and exact
prompts/schemas; every successful draft matches the original resolver projection.
Failed outcomes stay failed. Source and historical-result hashes remain unchanged.

This turn made nine presales inference calls, no automatic retries and no embedding
calls. Eight responses report 59,034 tokens in total; one failed stream has unknown
usage, so total tokens and billed charges are unknown. Separate documentation work
used one Smart Search query and two page fetches; these are not presales requests.

Postflight preserves staging rc49/application fe995676, DB0037, v15/Grok-low,
five ready services and zero active jobs. Ledger counts remain jobs3553,
attempts541, provider calls597, usage reservations541 and product reservations613.
UTC-day application dispatches are zero; known current-day direct calls are18.
Carrying18 prior-day reservations gives36/200 as a conservative ceiling, not36
current-day calls. No database, runtime configuration or deployment writes occurred.

The frozen nine-call cap is exhausted. Stop this comparison and prompt correction.
Keep Grok semantic acceptance explicitly open, centred on rule/fact distinction,
unknown state and original-source qualification preservation. Do not add another
schema, mandatory reviewer model or repeated sampling to claim completion. The
existing human-review/Excel delivery workflow remains available; GPT stays an
explicitly tested comparison channel and is not silently selected in production.

All original requests, results, inspections, reviews and the complete answer
comparison are retained under the existing central recovery group, phase
`grok_gpt_semantic_comparison_20261010`. Git baseline da791d6 recovers the five
scoped documentation paths; the new report is registered as originally absent.
No product code changes, temporary-file deletion or historical cleanup occur.
The task remains in progress.
