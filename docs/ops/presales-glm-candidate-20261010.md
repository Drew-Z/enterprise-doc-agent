# GLM candidate qualification, 2026-10-10

GP01 is not qualified for promotion. It correctly classifies all six prerequisites,
links real necessity rules, retains authorization conflict and excludes other-order
evidence. Its original draft_contract failure remains unchanged because three
responses each copy the complete question rather than contiguous ordered parts.

## Frozen observation and original outcome

One fresh synthetic case covers fictional 庭川设备服务, TC-6021, 90-day device-record
retention at 2026-10-08. States: licence met, policy unmet, restore verification
unknown/missing, storage authorization unknown/conflict, training met and required
confirmation-form submission unmet. TC-5990 cannot complete current-order gaps.

The request changes only model_name on the same primary endpoint/credential source.
Full assessment-candidate.v1 prompt/schema/resolver, low effort, streaming, 120-second
deadline and 4,000 requested tokens are unchanged. Input and 11 semantic criteria
were frozen separately before dispatch; the reference is assistant-authored, not
independent domain review. No fallback, embedding, replay, retry or output repair.

| Measure | Original result |
| --- | --- |
| HTTP / completion | 200 / stop |
| State | failed / presales_invalid_model_output / draft_contract |
| Time | 34.109 seconds |
| Input / completion / total tokens | 4,132 / 1,529 / 5,661 |
| Requested / returned model | glm-5.3 / z-ai/glm-5.3 |
| Response ID | chatcmpl-95b838c3-1c62-4da2-aec4-c0056dc22916 |
| Original decoded wire bytes | 69,430 |

Billed cost is unknown. A separate read-only GET /models lists glm-5.3 with
owned_by=custom, but verifies no canonical alias mapping. Record both names without
normalizing them or alleging a different underlying model. That GET adds zero
inference requests. No reliability or model-wide quality claim follows from one case.

## Offline inspection and semantic review

Complete saved source and offered span input are reconstructed from the frozen
dataset. Actual request system/schema are bound to the unchanged candidate. Strict
schema and every selected source identifier pass. Calling the original resolver
with the unmodified content raises exactly:

> responses must cover the exact requirement in order

All three requirementText values equal the full requirement. Their answer prose
addresses the overall decision, the six states/actions and other-order applicability;
the failure is mechanical coverage, not absence of all substantive answers.

Frozen criteria 2-7, 9 and 11 are satisfied. All six typed states are correct;
requiredBy selects actual rules; both conflict records survive; completed training
has null nextAction and no proof-submission duty. Criterion 1 fails coverage and
unresolved model identity. Criteria 8 and 10 are not fully satisfied because both
the verification nextAction and response include this alternative:

> 补充登记订单TC-6021的设备记录恢复校验通过结果，或确认其是否已通过。

The status is explicitly unknown, and confirmation is offered. However, the first
alternative presupposes a passing result without an explicit if-passed condition.
This is unresolved action ambiguity, not a wrong unknown-state classification or
an explicit false assertion that verification passed. Preserve partial successes
and this limitation; criterion counts are not accuracy or independent approval.

## Next repair and unchanged release

Implement a separate private candidate where the server offers immutable question
parts and the model selects their IDs. Require complete ordered coverage, materialize
only the original question text, and reuse the unchanged resolver. Lexical question
parts do not prove semantic completeness and cannot correct the action ambiguity.
Keep the GP01 first outcome failed and perform no further inference in this phase.

Read-only postflight keeps staging rc49/application fe995676649570328892e2b48d80f02ee9526dd8,
DB 20261010_0037, default v15, production Grok/low, five ready deployments and zero
active jobs. Policy/module hashes and ledgers remain jobs 3553, presales attempts 541,
provider calls 597, usage reservations 541 and product reservations 613. UTC
2026-10-10 has zero application dispatches and six known direct requests. Eighteen
prior-day reservations make a conservative ceiling of 24/200, not 24 current-day calls.

## Evidence and recovery

Recovery phase glm_candidate_qualification_20261010 uses d9c4257 and six registered
documentation paths in the existing central recovery group. Product sources and
all earlier outcomes retain their frozen hashes. Two controlled HTTP scenarios
passed; offline validation introduces no request. Scoped documentation/publication
receipts are central. The 2,993 unrelated workspace status entries remain intact.
No temporary or historical files were deleted; central evidence is a deliverable.

| Evidence | SHA-256 |
| --- | --- |
| Input | `9a946ec3274cf8ca45a307d5c7f2992789e8bc3985d129d177734eed1e262290` |
| Criteria | `8b80c9370dda51c63b6736498949f137a6e607410d839e70c2e8ea9fd9ae59e8` |
| Original result | `d3229ad89e081a43ef97b218e4996ad0ba7593f2fffa9f14325bee0efa0de1c6` |
| Inspection | `76de8b5c0cb455e088357d7cfa6d962a63d667092e3c41d0ab0c1831136a667d` |
| Review | `7855348df134b1bf2f21d89cd44f685574aecf2196988a7b89ff11c318984e4d` |
