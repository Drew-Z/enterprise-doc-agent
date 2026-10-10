# Minimal-contract observation, 2026-10-10

MC01 fails semantic acceptance even with a single answer field. The current primary
route still turns missing verification records into an unfinished-work task, adds
an unsupported training-certificate duty and leaves the overall decision unanswered.
Do not integrate this diagnostic or add another schema to address these findings.
No product code, production setting or staging application changed.

## Experiment and result

The new assistant-authored fixture describes fictional 澄岚档案服务, order CL-3106,
75-day scanned-document retention and a 2026-10-06 cutoff. Six frozen expectations
are licence met, policy unmet, reading-verification unknown/missing, storage-permit
unknown/conflict, training met and activation-form submission unmet. Other-order
CL-3072 records cannot fill its gaps. The reference remains separate from inference.

The request retains complete source text, names and applicability from
prepare_citations. It uses a 959-byte system prompt and JSON mode with exactly one
answer string, rather than linked rule, assessment and response arrays. It keeps
grok-4.7/low, the existing primary endpoint, streaming, the 120-second deadline,
4,000 requested output tokens and the 4,000-character answer limit. The complete
request is 4,569 bytes. A separate diagnostic decoder produces no public draft and
does not weaken the production validators or reinterpret historical reports.

Exactly one real request returned HTTP 200 with finish_reason stop in **32.0s**.
Reported usage is **1,236 input + 1,524 completion = 2,760 tokens**. Returned model
name is grok-4.7, response ID chatcmpl-4a041e53; billed cost is unknown. Original
state is schema_valid. It does not establish a successful product response.

Of 11 pre-frozen semantic criteria, only 6 and 9 pass: the answer preserves both
conflicting permit records and rejects substitution from the other order. Criteria
1, 2, 3, 4, 5, 7, 8, 10 and 11 fail. Examples from the unmodified answer:

> 扫描件读取验收：现状：未记载。……下一步：完成扫描件读取验收。

The record leaves actual completion unknown; the required next action is to confirm
whether verification passed, without assuming it remains to be done.

> 管理员操作培训：现状：已完成，但附件没有培训证书。……下一步：提交培训证书。

Training is complete and the necessity rule does not require certificate submission.
The answer also repeats the can-enable question without answering it and cites no
necessity-rule source. Correct conflict/scope handling does not outweigh the other
failures. The review is assistant adjudication, not independent domain approval.

## Bug Analysis: semantic errors survive simpler formatting

### 1. Root Cause Category

The confirmed process error is an implicit assumption (E): structurally separating
roles and enforcing literal selections was treated as a promising remedy for
semantic interpretation. Controlled schema tests cannot establish that remedy (D).
The remaining cause within the primary inference path is unresolved; this observation
does not independently verify provider weights or a universal model capability limit.

### 2. Why Fixes Failed

Strict schema addresses shape; mutually exclusive branches address support
combinations; span IDs address literal transcription. None establishes entailment,
source role or business obligations. AR01 exposed errors despite separate arrays.
MC01 exposes missing-as-unfinished and an invented duty without those arrays.
The latest output also demonstrates that removing coverage fields does not itself
produce an overall answer. Earlier mechanical improvements and failures remain intact.

### 3. Prevention Mechanisms

Freeze meaning criteria before inference, inspect the original action text and
retain failures separately from schema validity. Refuse runtime promotion here.
Stop adding schema/selection fields as a substitute for evidence of interpretation.
Source binding and byte limits remain necessary, independently tested boundaries.

### 4. Systematic Expansion

Prioritize an investigation of the existing primary route's inference settings and
observed answer quality before further product protocol changes. A separately
planned higher-reasoning observation may be informative while keeping endpoint,
budgets and semantic standards; nothing in this result changes staging configuration
or authorizes a retry of MC01. No fallback-channel experiment is part of this phase.

This is a fresh analogous sample with a changed prompt/output format, not a matched
ablation. It proves only that nested schema is unnecessary for these errors to occur
in this observed route. It does not quantify protocol effects, establish the dominant
cause, prove a broad model limitation or demonstrate a latency/cost improvement.

### 5. Knowledge Capture

The task PRD, design, execution/validation records and presales-quality specification
record this boundary and the next direction. This repository has no
src/templates/markdown/spec tree to synchronize. Public-task repair, independent
review, actual user time savings and competitor acceptance remain open.

## Validation, provenance and recovery

The initial collector check failed before dispatch because collection was not yet
implemented. Five controlled HTTP cases then pass: streaming success, HTTP 503,
wrong answer type, incomplete completion and cancellation. Each dispatches once,
retains first/final state and never retries. Cancellation stays interrupted_unknown.
The collector reuses bounded stream reading, recording, credential loading and
read-only staging preflight; no credentials are recorded. These checks do not
measure model semantics.

Offline inspection binds the five full source records and exact request to the
frozen dataset, confirms the answer equals the raw response and checks source/helper
hashes. The report identity is presales-minimal-contract-run-v1, outside historical
scorers. Baseline is 9b46e81aa1aecbb9e288c5bf7336ad6ecda82413.

| Evidence | SHA-256 |
| --- | --- |
| Input | `d7b6188af46152c2cc46c6b9b8c223c266af79bb033c3b3c911247d0333b7c07` |
| Criteria | `6d4d9c84703e255a9bfc60316665e5e074729995e3ae03f40ab5bfa3b0425c88` |
| Exact request | `f942ed6fe38b10e41848345a2f71fc9082f2da1720f60968e7b9ba753abeccc5` |
| Original result | `2f9c0ebe13564de846819ec065c2853d548eb00293ac04e18e8f4f8e62cae3e9` |
| Semantic review | `1f5cfaea66d65501692caea516879dfbd477e7d0b5c6932b6765da8ed87eeec1` |

Fresh postflight confirms unchanged staging source fe995676649570328892e2b48d80f02ee9526dd8,
rc49, DB 20261010_0037, default v15, five ready deployments and zero active jobs.
Policy/source hashes and ledger counts remain unchanged: jobs 3,553, presales
attempts 541, provider calls 597, usage reservations 541 and product reservations
613. UTC 2026-10-10 has zero application dispatches and three known direct calls;
18 prior-day reservations yield a conservative 21/200, not 21 current-day calls.
No embedding, fallback, deployment, retry, source repair or historical replay.

Recovery phase minimal_contract_observation_20261010 in the existing commercial
group records Git/new-file recovery for six documentation paths plus central
controller and evidence. Product code is unchanged from the baseline with passing
CI; no additional local full-regression run is claimed. Scoped documentation,
publication and exact new-head CI receipts are recorded centrally. The 2,993 unrelated
status entries are preserved. No disposable files or historical files are removed.
