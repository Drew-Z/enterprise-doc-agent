# Primary reasoning comparison, 2026-10-10

Both requested reasoning settings fail the frozen semantic gate. The low arm turns
an unregistered test result into a failed test; medium instead presupposes a passing
result. Both invent a training-transcript submission duty. Do not promote medium
or change production configuration based on this observation.

## Prospective paired design

RC01 is a fresh assistant-authored synthetic requirement about fictional 松屿监测,
order SY-4820, 60-day event-copy retention and a 2026-10-07 cutoff. Its six required
states are subscription met, retention configuration unmet, replay test unknown/
missing, storage authorization unknown/conflict, training met and go-live-list
submission unmet. Another order, SY-4795, cannot fill current-order gaps.

Both requests and the separate 11-criterion reference are frozen before inference.
Source text, applicability, citation IDs, system prompt, model name, primary endpoint,
streaming and output contract are identical. Only reasoning_effort differs. Order
is fixed in advance: medium, then low. Each gets one first request with the same
120-second deadline, 4,000-token request cap and 4,000-character answer limit.
No first answer enters the second input. This is not a retry of MC01 or AR01.

The same central minimal collector is reused unchanged. A wrapper records both
planned arms and their first/final states, reserves two calls upfront, and rechecks
current policy, ledger and active work before each. Failure, unknown outcome or
drift stops later arms without retry/resume. No product draft is constructed.

## Original results

| Observation | Requested medium | Requested low |
| --- | --- | --- |
| HTTP / completion | 200 / stop | 200 / stop |
| Minimal format | schema_valid | schema_valid |
| Elapsed seconds | 32.438 | 24.531 |
| Input tokens | 1,260 | 1,260 |
| Completion tokens | 1,831 | 1,433 |
| Total reported tokens | 3,091 | 2,693 |
| Response ID | chatcmpl-3fb8c29e | chatcmpl-e110c001 |
| Returned model name | grok-4.7 | grok-4.7 |
| Semantic acceptance | Rejected | Rejected |

Total reported usage is 5,784 tokens; billed cost is unknown. The submitted effort
parameter is verified, but the provider's internal enforcement is not independently
attested. No broad latency, cost, reliability or model-capability claim follows from
this single sequential pair.

Medium's original text says the test result is unregistered, then:

> 下一步：需登记通过结果。

It does not establish whether the test actually passed. For completed training it
simultaneously says no new duty and introduces one:

> 下一步：已完成，无需新增，但需附上成绩单以符合要求。

Low explicitly states:

> 事件回放测试现状：未通过

and also requires submission of the training transcript. Neither the negative test
claim nor the extra transcript duty is established by the supplied sources.

Both answers do provide the overall cannot-enable-now decision, preserve conflicting
authorization records and reject other-order substitution. Medium passes criteria
1, 5, 6 and 9; low passes 1, 6 and 9. Criterion 5 narrowly tests that absence of a
pass record is not explicitly equated with failure. Medium avoids that specific
negative claim, but its implied pass still fails the unknown-state/action criteria
4 and 8. No criterion was changed after inference. These counts describe frozen
criteria, not sample accuracy or a statistically meaningful improvement.

Review is assistant adjudication against the pre-frozen reference, not blind or
independent domain review. Both original outputs, partial successes and failures
remain separate from the mechanical inspection and unchanged.

## Decision and next investigation

Requested medium has not demonstrated a usable remedy. Keep the deployed low
configuration, all historical failures and both first outcomes. Do not add another
schema or keep changing effort levels in response to this case.

A subsequent read-only GET /models on the same primary endpoint advertises exactly
grok-4.7 and glm-5.3. It performs no inference. The next investigation is a separately
planned qualification of glm-5.3 as an alternate primary model on that same endpoint,
retaining semantic standards and existing call/output limits. Its actual inference
availability, protocol compatibility and quality remain unverified; no model switch
or additional inference occurs in this phase. A future successful diagnostic would
still need actual product-contract and public-task validation before promotion.

Public-task repair, independent review, user time savings and competitor acceptance
remain open. This comparison changes the next investigation, not the deployed product.

## Validation, provenance and recovery

The first local paired test fails before dispatch at absent orchestration. Four
controlled scenarios then pass: successful independent arms, HTTP 503 stopping the
second arm, cancellation preserving unknown and stopping the second, and preflight
drift before the second. Requests differ only in effort; first-output leakage is
absent. These checks exercise orchestration rather than model reasoning.

Offline inspection binds both complete source projections and exact request bodies
to the frozen input, validates the minimal schema and confirms each reviewed answer
is the original provider text. The report identity is
presales-reasoning-comparison-run-v1; it is not accepted by historical scorers or
passed into application persistence. Baseline is
144497912618a8e60e719b82fd35a1600951415c, with product sources unchanged.

| Evidence | SHA-256 |
| --- | --- |
| Input | `e545aaf9b021f622450a09a1788bb04f958becf57bbfe7717d8163259e499062` |
| Criteria | `9d7e9188a003dbf6660debc30a79e94692fa6364016a470e8ac2e6b17b4fd1d5` |
| Original paired result | `862ff888efd8670a522639c06f4416a0c84afed70be16c6e663e038fa446eb04` |
| Semantic review | `fc4db24aca3daf17498e1f4329a5623e9e2e9251ec62c43b9baba6ebf96465cc` |

Fresh postflight retains rc49, application fe995676649570328892e2b48d80f02ee9526dd8,
DB 20261010_0037, default v15, five ready deployments and zero active jobs. Policy,
source hashes and ledgers remain unchanged: jobs 3,553, presales attempts 541,
provider calls 597, usage reservations 541 and product reservations 613. UTC
2026-10-10 has zero application dispatches and five known direct calls. Carrying
18 prior-day reservations gives a conservative 23/200, not 23 current-day calls.
Two new inference requests, one separate metadata GET, zero embedding, fallback,
retry, historical replay, source repair or deployment are recorded distinctly.

Central phase reasoning_comparison_20261010 registers six documentation paths with
Git/new-file recovery at the baseline, plus controller and evidence artifacts.
Existing runtime sources and original v18/v19/v20/AR01/MC01 outputs retain their
hashes. No new local full-regression run is claimed for unchanged product code.
Scoped documentation checks, publication and exact new-head CI receipts are central.
The 2,993 unrelated status entries are preserved. No temporary resources or
historical files are deleted; central evidence is retained as a deliverable.
