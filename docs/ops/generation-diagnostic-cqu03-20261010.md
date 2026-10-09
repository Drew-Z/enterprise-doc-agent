# CQU03 generation diagnostic — 2026-10-10

The existing primary route produced one schema-valid answer, but the answer did not
fully cover the frozen public requirement. Model quality and competitor acceptance
remain open. This is a generation-only diagnostic, not another competitor comparison,
customer validation, retrieval test or repeat of the frozen SWU failures.

## Implementation

`scripts/evaluate_presales_gateway.py` now retains the gateway's optional allowlisted
`errorDiagnostic` in failed observations. Previously only the broad errorCode survived.
Transport failures without a category remain uncategorized. This does not change the
product gateway, prompt, routes, retries, public schema, accounting or stored histories.

The HTTP regression failed with `KeyError: errorDiagnostic` before the two-line fix;
the related collector, streaming, historical scoring and diagnostic suites then passed
49 cases. Credentials stay excluded and an existing output still refuses overwrite.
Full checks passed: Ruff format739/check, Mypy274 sources,3,029 nonintegration tests
and23 subtests (810 deselected,266.05s). No frontend code changed.

## Frozen input and first outcome

The requirement is CQU03, “说明上线前通过学校组织安全检测的安排与证明状态”, from the
previously collected [CQU procurement document](https://ztbzx.cqu.edu.cn/sfw_cms/r/s/cms/76c7edb7203c404d8ebc49a4f60ebf6c/0).
It is a closed December 2024 procurement, with no supplier completion records supplied.
The offered literal clause requires school security testing before launch, remediation
after a failed test, and a passing result before launch. This single short source does
not test multi-source reasoning or difficult retrieval. The publication timestamp in
the fixture comes from the retained capture file metadata; no fresh web fetch occurred.

Input, separate reference and stop rule were frozen before inference. The configured
primary matches staging auto: grok-4.7, low reasoning, streaming, 120 seconds,
presales.v15 SHA `318fc29ef2903cef5ad51a59163fad35ff855aca012bbae986e84a0fbb83d2ab`.
The local collector made exactly one request, no fallback/retry and no embeddings.

| Observation | Result |
| --- | --- |
| Original completion | 40.718 seconds; schema-valid draft |
| Status and exact citation | insufficient_evidence; required clause present |
| Current state | unknown; no invented pass, failure, schedule or commitment |
| Answer coverage | Incomplete: testing arrangements were omitted |
| Conditional requirement in prose | Failed-test remediation was omitted |
| Prerequisite meaning | A known necessity rule was phrased as the proposition and assessed unknown; the unknown should concern actual completion |
| Usage | 2,534 prompt / 1,831 completion / 4,365 total reported tokens; currency cost unknown |

The mechanical scorer passed classification/source checks. The separate Codex-assisted
semantic review marked the result incomplete against the pre-frozen reference. This is
not independent domain adjudication. Do not infer a reliability rate or attribute the
historical SWU failures to the newly observed semantic problems.

## Operational boundaries and next action

Read-only observations before and after confirm unchanged rc49 application sources,
schema0037, policy, five ready workloads and global accounting: jobs3553, attempts541,
provider calls597, usage reservations541 and product reservations613. The application
daily dispatch counter remains36 because this request is outside the product ledger.
Conservative daily consumption is50/200:36 application +13 prior known/reserved direct
calls +1 new request. Three prior unknown calls remain reserved;50 is not a confirmed
provider execution count. Frozen public packets were not modified.

The next quality change must address complete requirement coverage and business-event
assessment versus rule definition. Define those behaviors before changing a prompt or
protocol; this run supplies a concrete failing example. Do not add another channel,
increase budgets, relax evidence validation, or rerun this diagnostic to obtain a pass.
The original six-row replay remains4 drafts /2 failures /9 calls, and the old lost
diagnostic remains unknown. No new staging deployment is needed for this evaluator fix.

Recovery uses the existing commercial group, phase `generation_failure_diagnostic_20261010`,
with seven exact Git baselines at `58c1e5c8f35da056e4f21552b5cd5fa75e59f2ab` and this new
report recorded absent. Central `fallback-model-evidence/cqu03-diagnostic-*` files retain
the input, criteria, preflight, plan, intent, original output, mechanical score, semantic
review and postflight. Input SHA: `825d304511576bbed4684b18a7ff3f21f4c16491aaa175ee1a26917bc9eb5f5f`;
original result SHA: `9ac026a5660037a09d111a8aff49a90f08c1a6d13ad1e19aca83ed1acad0a580`.
