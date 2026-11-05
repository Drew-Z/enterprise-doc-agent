# Question candidate observations, 2026-10-10

QP01 passes the frozen mechanical and semantic checks for this one synthetic case.
It returns three ordered question IDs and preserves all six evidence states. This
permits a separately frozen public-task observation; it does not qualify runtime
promotion, establish reliability or change any earlier failure.

## QP01 design and original result

The new case describes fictional 岚桥访客记录 service, order LQ-7142, 45-day visitor-log
retention at 2026-10-09. Rule order is licence met, integrity verification unknown/
missing, retention setup unmet, operator training met, regional authorization
unknown/conflict and signoff submission unmet. Other order LQ-7116 cannot fill gaps.
Compared with GP01, order and missing-result wording change; this is not a matched
ablation proving why quality improved.

The candidate source/system/schema remains frozen at b678ce4. A dedicated adapter
composes existing source-span offering with server-owned question parts and calls
the unchanged resolver. Only diagnostic model_name is overridden to glm-5.3 on the
existing primary endpoint/credential source. Low effort, streaming, 120-second
deadline and 4,000 requested tokens remain. No live retry, output repair, embedding,
fallback or deployment is performed.

| Measure | First observation |
| --- | --- |
| HTTP / finish / original state | 200 / stop / succeeded |
| Elapsed | 28.157 seconds |
| Input / completion / total tokens | 4,449 / 1,806 / 6,255 |
| Requested / returned name | glm-5.3 / z-ai/glm-5.3 |
| Response ID | chatcmpl-0f92e46b-8756-4de8-830a-c7a2084d6bbf |
| Question parts / assessed prerequisites | 3 / 6 |

Billed cost is unknown. The returned name is the same unverified alias seen in GP01;
no canonical identity mapping is established. It remains a separate runtime-promotion
gate, not silently normalized or dropped from acceptance.

## QP01 original meaning review

All 11 criteria in the independently frozen reference are satisfied by assistant
review. Every requiredBy selects a true necessity-rule clause. Both authorization
records remain without invented priority. Training is met with null nextAction and
explicitly no proof-submission duty. Overall conclusion and three answers preserve
the current order and reject substitution from the other order.

The unknown verification action is now:

> 向归档完整性校验的执行方核实校验是否已通过，并将结果录入台账。

This asks for the actual outcome before recording it; it does not ask to record an
assumed pass. Authorization remediation is explicitly conditional on establishing
that authorization is ineffective. Both completed items have no new tasks. The
propositions include necessity wording, but the state, summary and action clearly
evaluate the underlying business event, with no invented extra rule.

Offline inspection reconstructs the original full source/question input, verifies
actual system/schema and every selected ID, and calls the original candidate resolver
on unmodified output. The projected draft exactly matches the saved result. No
historical scorer is used; report identity is presales-question-candidate-run-v1.
Neither assistant review nor literal coverage establishes independent domain approval
or a population accuracy rate.

## Controls, unchanged application and recovery

Initial local checks fail before live dispatch at the absent adapter, then at a
collector identity binding that still expected the prior candidate. Scoped identity
binding is fixed; controlled success, duplicate-ID rejection, HTTP 503 and cancellation
all pass with one mock dispatch and restoration of original gateway/collector identity.
Original candidate and product sources are not edited.

QP01 postflight retains rc49/application fe995676649570328892e2b48d80f02ee9526dd8,
DB 20261010_0037, default v15/Grok-low, five ready deployments and zero active jobs.
Policy/module hashes and ledgers remain jobs 3553, attempts 541, provider calls 597,
usage reservations 541 and product reservations 613. UTC 2026-10-10 has zero application
dispatches and seven known direct requests; carrying 18 prior-day reservations gives
25/200 as a conservative ceiling, not 25 current-day calls.

The observation registers six documentation paths at b678ce4 in the existing central
recovery group. Validation/publication receipts and raw outputs remain central.
The candidate's 31 new tests, 114 focused tests, 3,189 nonintegration tests and exact
b678ce4 CI were already passed in its implementation phase; unchanged product code
is not claimed to have a fresh full-suite run in this observation phase.

## QP01 frozen evidence

| Evidence | SHA-256 |
| --- | --- |
| Input | `74966bbd0bf22cc219ffdc58f0caee394f3806e94bafdc6f99525070feb7e603` |
| Criteria | `ff71c4b3abb9f73e34d3038fea97fa8d8b352b3df72505ef3a9b6cf1049766a9` |
| Original result | `6b29db19bea3a4fc7eae0f5c7a4f15f2184181156feb1edb44dc8cb830b3604f` |
| Inspection | `34503fee34bb6d9e3301a3c83dea0902a9296840ca980719ac40d71cd842f3b2` |
| Review | `c2a5df89793b59cc256e15ae98d1d4746b5307020ee44235db009aa0e298a8e6` |

## Two real public procurement clauses

After QP01 review, a separate phase froze CQU02 performance and CQU05 team
qualification from the December 2024 Chongqing University / University of Cincinnati
Joint Institute procurement. This is a closed historical procurement, not a current
bid or a customer engagement. The official source was refreshed using:

```powershell
smart-search fetch 'https://ztbzx.cqu.edu.cn/sfw_cms/r/s/cms/76c7edb7203c404d8ebc49a4f60ebf6c/0' --format json --output '<central-evidence>/question-public-cqu-source-20261010.json'
```

The Tavily fetch returned 21,901 characters in 12.675s without fallback. This is one
separate research-service fetch, not a presales inference request. Exact relevant
sections, byte hashes and character offsets are retained before model dispatch.
[Official procurement source](https://ztbzx.cqu.edu.cn/sfw_cms/r/s/cms/76c7edb7203c404d8ebc49a4f60ebf6c/0).

The questions come from the existing public-task pack. A targeted scan of local
result/intent/input receipts found no earlier CQU02/CQU05 execution; the old pack's
not_run flags alone are not treated as proof. Inputs contain only real buyer
clauses, with no supplier performance report, personnel records or invented supplier
facts. The expected task is a useful evidence-gap response, not a compliance claim.
This direct generation observation does not test retrieval, UI or persistence.

Both inputs and their separate eight-criterion references were frozen before either
request. The batch preserves planned/unattempted cases, reserves two calls upfront,
checks current policy/ledger/source identity before each and stops after failure,
unknown or drift. Four local batch scenarios verify both successful calls, first
failure, cancellation and drift before the second. The unchanged QP01 adapter handles
the real boundary; no first output enters the second request.

| Case | Original result | Seconds | Input / completion / total tokens | Semantic result |
| --- | --- | ---: | --- | --- |
| CQU02 performance | HTTP 200 / stop / succeeded | 9.750 | 2,833 / 423 / 3,256 | Incomplete test-condition request |
| CQU05 team | HTTP 200 / stop / succeeded | 34.907 | 3,845 / 584 / 4,429 | Required experience category relaxed |

Response IDs are chatcmpl-d72642c4-2cd7-4242-b5d4-8eef2b868122 and
chatcmpl-5f19c1e2-97a2-498b-8a90-50901bb26222. Both request glm-5.3 and return
z-ai/glm-5.3; canonical mapping remains unresolved. Public calls report 7,685 tokens;
with QP01 this turn adds three presales inference calls and 13,940 reported tokens.
Billed charges remain unknown. These timings are observations, not a speedup or
human-time-saving comparison.

### Performance: correct uncertainty, incomplete conditions

CQU02 correctly preserves the buyer's 5-second and 200-above-concurrency wording.
Both supplier states are unknown/missing and overall status is insufficient_evidence.
It asks for reports, test environment, network conditions, load, concurrent users and
tools. No fabricated test result, same-system performance assertion or architecture
substitution occurs. Original references select the true buyer clauses. Although
the response's citation array is empty, requiredBy retains the relevant exact source
in the projected public draft; no citation loss is alleged.

Frozen criterion 7 is only partly satisfied: the requested evidence list does not
explicitly include resource configuration and the measurement method. The source
does not provide these conditions, so this is an incomplete clarification request,
not a false statement of actual conditions. Other seven criteria are satisfied.

### Team: a substantive qualification weakening

The technical clause says:

> 项目经理需要持有PMP 管理资质，且不少于10 年的工作经验。

The business clause further says:

> 项目经理应具有需 有10 年以上软件项目管理经验，技术负责人员应具有5 年以上开发、分析设计经验。

The original generated nextAction instead asks for:

> 10年以上工作经验（或软件项目管理经验）的证明材料

The rule/answer uses a slash between the categories; the summary/conclusion and final
gap list keep only general experience. This makes general tenure an alternative to
the specifically required software project management history. Correct citations
and a correct unknown state do not cure that change in obligation. Frozen criteria
7 and 8 fail; six other criteria pass. It does correctly retain PMP, the seven-person
core team including manager and technical lead, absence of supplier records and
insufficient_evidence; no fictitious people or certificates are asserted.

## Review-only suggested corrections

The following are assistant review suggestions, not modified provider outputs, saved
product reviews or independent business approval. They do not replace the original
results or change the acceptance verdict.

- CQU02: ask for the target environment and resource configuration, covered page/request
  mix, concurrency definition and duration, measurement method and actual results
  for both thresholds. Do not invent a percentile or test standard absent from the
  buyer document; clarify the agreed measurement basis.
- CQU05: request project manager PMP evidence, general work history and specifically
  the software project management history required by the business clause. Keep the
  two original experience descriptions separately traceable; general tenure cannot
  substitute for the specific category. Also request the core-team roster/roles,
  resumes and certificates, preserving the seven-person inclusive team scope.

The next intervention is source-grounded review of generated obligations and evidence
requests, especially altered categories, conjunctions, numbers and scope. Investigate
it separately from generation, retaining original drafts and explicit review findings.
Do not respond by expanding the output schema again or repeatedly sampling the same
answers until one passes. Any review-stage model evaluation must first freeze detection
criteria and controls; it is not independent domain approval or permission to rewrite
an original outcome. No new product code or extra inference is added here.

## Public postflight and decision

Both original mechanical successes remain succeeded. The public semantic batch does
not pass, so do not promote this candidate or switch staging models. QP01's synthetic
pass remains a limited separate result; it does not override public-task failures.
The original deployed public replay remains 4 drafts / 2 failures / 9 calls.

Fresh postflight retains the same rc49/0037, five ready deployments, zero active jobs,
policy/module hashes and ledger counts listed above. UTC 2026-10-10 has zero application
dispatches and nine known direct requests. Carrying 18 prior-day reservations gives
27/200 as a conservative ceiling, not 27 actual current-day calls. No database write,
embedding, fallback, retry, historical-result repair or deployment occurs.

The public phase reuses this ongoing task's original six documentation recovery
entries; it does not snapshot edits as a replacement baseline. Original q-candidate
sources and all earlier evidence remain unchanged. Scoped documentation/hash checks,
exact-head CI and publication receipts are central. The 2,993 unrelated status entries
are preserved. No disposable files were created or deleted; historical files remain.
The task and parent competitor-standard goal remain open.

## Public frozen evidence

| Evidence | SHA-256 |
| --- | --- |
| Official fetch | `4bd54253b7021f4d6cd74ef6e8dc8ad1eef51942ca044507969da1ea571904d2` |
| CQU02 input | `2b3e2aa0721d6de62589e63b64c1a2b26f2d0d86db99b82679f4364642f6ef2d` |
| CQU02 reference | `e12cf65015db14aafed0d7b25e1bcfc0b4f261bbe7656fbb9bb34c55204d4d59` |
| CQU05 input | `ea5a544498738915a11f7618229d2782ba0d2708ff3ac729f9fef801eeb3eb39` |
| CQU05 reference | `6ffa74d3f54f31e55a0087eea080dd6be8877d92ae17d39cb99cf3c534335899` |
| Original batch | `980dd47d6af089ccaf410f5a7663f3fd8097de307cfc9aaa590c4809655621e1` |
| Inspection | `3c759208712845fd72a0761df6ff1dff979c6338888a36c4a543c81f9f7a1a70` |
| Review | `df9cb2bfa01e34dbcff0a473851dd4c532829fd33c3b3f658d434cfc1ada5b71` |
