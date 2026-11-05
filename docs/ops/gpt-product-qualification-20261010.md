# GPT through the current product contract

GPT remains the preferred replacement candidate, but a direct primary-route switch
is not qualified by this observation. The user has authorized replacement if better;
no repeat permission is needed. No runtime, configuration or credential was changed.

Unlike the earlier question-candidate comparison, this run used the unmodified
product gateway with the deployed v15 prompt/schema and literal-basis resolver.
The three known regression inputs and the earlier major-error criteria were frozen
before dispatch. Complete sources were provided directly; retrieval, persistence,
review/export and the background failover coordinator were not exercised.

| Input | Original outcome | Time | Assessment |
| --- | --- | ---: | --- |
| CQU02 performance | Accepted draft; returned `gpt-6-luna` | 62.125s | Correct insufficient evidence and both thresholds; detailed resource/measurement questions. However, the request to explain testing conditions becomes a business prerequisite unsupported by the cited necessity rule. |
| CQU05 personnel | HTTP502; no draft | 8.406s | Channel/upstream failure; no semantic result or usage is available. |
| QP01 mixed states | Not attempted | — | The predeclared channel-failure stop rule ended the batch. |

The CQU02 question is “核实页面响应时间5秒以内且支持200以上并发访问量，说明测试条件。”
The only source is “4. 性能要求 页面响应时间应控制在5 秒以内；系统应支持200 以上的并发访问量。”
The answer correctly declines to confirm supplier compliance. But a generated
prerequisite says “已说明用于核实页面响应时间和并发访问量的测试条件。” and uses the
performance clause as its necessity definition. Asking for these details is useful;
representing the question as a source-defined business obligation is the error.
The original output remains unchanged.

## Verified boundaries

- One real success used `max_tokens=4000`, empty tools, `tool_choice=none`, JSON mode,
  low effort and streaming, without the earlier transport adapter. Basic compatibility
  does not by itself require an adapter/new schema; universal compatibility is unproven.
- Both full inputs, wire requests and prompt identities match their frozen sources.
  Offline decoding exactly reproduces CQU02. CQU05 remains failed and QP01 unattempted.
- A controlled public-gateway HTTP502 call yields `presales_model_failed`,
  `retryable=true`, one dispatch and no gateway retry. Staging's existing policy
  allows primary then fallback, at most two dispatches. This direct evaluation
  does not run that coordinator or prove real GPT-to-fallback recovery. One502 is
  neither a product-wide failure rate nor a reason to remove bounded failover.
- Earlier GPT candidate results remain three useful drafts with better central
  state/uncertainty handling than the paired Grok outputs. Different output contracts
  prevent treating those results as qualification of the deployed v15 pipeline.

## Decision

Keep GPT as the replacement candidate, without switching this time: the observed
prerequisite-role error remains and current-contract personnel/mixed-state behavior
is unqualified. Do not start another model survey, introduce a mandatory review model,
or repeat samples until success. Any integration must preserve the distinction
between requested answer details and source-defined prerequisites and verify the
existing save/review/export path before the authorized switch.

Read-only preflight/postflight retain rc49 source
`fe995676649570328892e2b48d80f02ee9526dd8`, schema `20261010_0037`, v15, primary Grok
low/120s, existing fallback medium/180s, five ready deployments, zero active jobs
and unchanged application ledgers. No model/configuration, schema or historical
packet change, retrieval, embedding, review or export occurred.

Two new direct requests, no retries. CQU02 reports 3,693 tokens; CQU05 usage is
unknown. Known current-day direct requests increase from18 to20. The conservative
ceiling is39/200:20 current-day direct requests,18 carried prior-day reservations
and one unused planned reservation. Application-day dispatches remain zero; this
is not39 actual current-day calls.

Recovery uses the existing central group, phase `gpt_product_qualification_20261010`,
and Git baseline `e7a18a5af4a2d063f8beb8caf8087c41321d8d1c`. Original evidence uses
prefix `fallback-model-evidence/gpt-product-*-20261010`: plan, intent, per-case
result/request, inspection and postflight. Evidence is retained as deliverables;
no disposable files or historical cleanup were needed.
