# Assessment candidate observation, 2026-10-10

The first actual observation of `presales.assessment-candidate.v1` fails both
candidate projection and semantic acceptance. Do not integrate this candidate.
The request returned normally; correct JSON and literal source selections did not
produce a usable answer. Production remains on rc49 with default v15 and strict
mode disabled. No deployment or product-source change occurred in this phase.

## Frozen scope and execution

AR01 is a new assistant-authored synthetic requirement for fictional 岑禾票据服务,
order CH-2610, 120-day retention and a 2026-10-02 cutoff. It asks whether the order
can start, the state and next action for six prerequisites, and whether records
for CH-2599 can fill its gaps. It is not a public task or actual customer evidence.
The reference was frozen separately before inference and never sent to the model.

| Prerequisite | Frozen expected state and action |
| --- | --- |
| Institution licence | Met; no new action |
| 120-day retention policy | Unmet; configure the policy |
| Export verification | Unknown/missing; establish whether it passed and obtain the result |
| North China storage authorization | Unknown/conflict; reconcile same-time peer records |
| Operator training | Met despite absent certificate; no certificate-submission duty |
| Handover list | Unmet; explicitly required submission is still outstanding |

CH-2599 records cannot establish completion for CH-2610. All scope, role, state,
answer and action criteria were fixed before the single dispatch.

The dedicated adapter reuses the production gateway transport, streaming, source
offering and limits. Scoped bindings substitute only the candidate prompt, schema,
identity and resolver, then restore the original v20 identity. Controlled HTTP
success/503 cases each make one mock dispatch without retry. An initial fixture
metadata error was corrected before freezing and before any dispatch.

Exactly one real primary request used grok-4.7, low reasoning, streaming and the
existing 120-second timeout. It returned HTTP 200 in 103.5 seconds, finish_reason
`stop`, with 5,532 input and 3,458 completion tokens (8,990 total). The provider
reported grok-4.7 and response ID `chatcmpl-5c6372ba`. Billed cost is unknown.
There was no fallback, retry, embedding, retrieval or application persistence.

## Original result and semantic review

The original state is `failed`, error `presales_invalid_model_output`, diagnostic
`draft_contract`. Resolving the original bytes offline reproduces:

```text
responses must cover the exact requirement in order
```

Every response's requirementText copies a source necessity rule instead of the
customer question. Strict JSON Schema/Pydantic validation, full source binding,
all 17 offered spans and selected span IDs pass. The top-level
`conflicting_evidence` matches the reference. There is still no accepted draft.

Separate assistant review against the 11 pre-frozen criteria passes only criteria
3 and 6: requiredBy selects actual necessity rules, and authorization preserves
both conflicting records with a clarification action. Criteria 1, 2, 4, 5, 7, 8,
9, 10 and 11 fail. Key failures are:

- Propositions repeat normative "必须" rules rather than affirmative completion events.
- An unregistered verification result is classified as unmet. The action asks to
  register a passing result without first establishing whether verification passed.
- Training is structurally met with a null action, but prose requires attaching
  a certificate and the conclusion treats its absence as noncompletion.
- The conclusion and four answers claim CH-2599 records can fill CH-2610 gaps,
  contrary to the source applicability boundaries.
- The conclusion also calls already-met licence and training deficient.

Configuration and handover actions are concrete, but those limited successes do
not outweigh the failed criteria. This is assistant review, not independent domain
approval, and one observation supplies neither a reliability rate nor competitor
parity evidence. The original report and the review remain separate and unchanged.

## Decision and next investigation

Reject candidate promotion and preserve the standalone committed code. Do not
relax coverage, relabel missing as unmet, repair the answer, replay AR01 or alter
historical outcomes to obtain a pass. More schema/selection fields alone have not
established semantic improvement.

Before further product changes, plan a minimal generation contract on the same
primary route to distinguish protocol overload from basic interpretation failures.
Preserve the semantic standards, source context, first-outcome reporting and budget
limits. This is a direction for a separate investigation, not a second request in
this observation, a decided cause, or permission to change runtime behavior.
Public-task quality, independent review, user time savings and competitor acceptance
remain unfulfilled; the parent task remains active.

## Provenance, validation and recovery

The source baseline is `fca01bbc14676dbc0244ad787ded3ca902ef784e`. Candidate prompt
and schema identity SHA is
`f274eeba7c7f7c32b7420c2c2d0f37246f170bde0504f496b0f3bf8677f13b07`.

| Artifact | SHA-256 |
| --- | --- |
| Frozen input | `5f7e42511acd9f7898fa6ebf95e3f6e8bc991eafea5e66db324a4df4713d2d37` |
| Frozen criteria | `d3e3b5bfec24ca447815910f68885c049e75bb59b05950d5a3bf27bae6c62f7e` |
| Original result | `a3153f653869ab85b2a15c457e8558ebf67117f21c878ebac86d9ade69f059f1` |
| Mechanical inspection | `62be0d766c7e2f4361a154fce756e6ba35175e36aee14c56472603a61c1e71e7` |
| Semantic review | `16b77cf9eeecd49338c190ae94ffb049ad1d7c70aa746516bd73f68988fc9def` |

The distinct report format is `presales-assessment-candidate-run-v1`; it must not
be relabeled as run-v10 or passed to unsupported historical scorers. The central
commercial recovery group holds the controller, input, reference, plan, intent,
original result, inspection, review and read-only postflight evidence.

Postflight confirms staging source `fe995676649570328892e2b48d80f02ee9526dd8`,
rc49, DB `20261010_0037`, default v15/strict off, five ready deployments and zero
active jobs. Policy, source hashes and ledgers are unchanged: jobs 3,553,
presales_attempts 541, presales_provider_calls 597, usage_reservations 541 and
product_usage_reservations 613. UTC 2026-10-10 has zero application dispatches and
two known direct requests. Carrying 18 prior-day known/unknown reservations yields
a conservative 20/200 ceiling, not 20 actual current-day calls.

Product sources and the original v18/v19/v20 result hashes are verified unchanged.
The baseline's 3,158 nonintegration tests, 23 subtests, Ruff and Mypy passed, as did
its [Quality](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/38010763206)
and [Container Supply Chain](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/38010763195)
checks. This documentation phase does not claim a new local full-regression run.
Scoped documentation checks and exact publication-head CI receipts are retained
in manifest phase `assessment_candidate_observation_20261010`.

Six registered documentation paths use verified Git recovery at the baseline or
recorded new-file absence. The 2,993 unrelated Git status entries retain their
baseline checksum. No disposable resources were created; central evidence is
retained as a deliverable. No historical file or image-cache cleanup was repeated.
