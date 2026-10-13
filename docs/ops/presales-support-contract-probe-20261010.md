# V19 mixed-state observation, 2026-10-10

The new strict contract is still not eligible for staging enablement. One fresh
synthetic request returned schema-valid output but failed exact-quote validation
and semantic criteria. Preserve the failed result; no retry, normalization, old
sample replay or additional route is used to turn it into a success.

## Frozen experiment and result

S1901 asks whether one fictional order can enable 90-day contract archiving and
requests the state and next action for four prerequisites. Four offered sources
contain the rules, order state, and contradictory peer authorization records with
the same cutoff and no stated precedence. Input and a separate reference were
frozen before inference. The collector never receives reference answers.

Exact candidate: `d74df4ef867087eb4fa74435e52a15e52a284562`, presales.v19,
prompt SHA `655d974ce22b099f135110c5be5fe0c766132960e48e39b41f38760095ea2249`.
The existing primary route, low reasoning, streaming and120-second timeout were
retained. One request completed in39.765 seconds, HTTP200, with5,004 prompt and
2,142 completion tokens reported by the provider. Cost remains unverified.

JSON Schema and Pydantic accept the returned support combinations. The unchanged
business resolver rejects `support_quote`: two quotations prepend shared date or
order context to later clauses, producing strings absent from the cited source.
The saved run-v9 observation remains failed with zero accepted drafts.

| Event | Frozen expectation | Original observation |
| --- | --- | --- |
| Enterprise identity verification | met | Correct positive evidence |
| 90-day retention configuration | unmet | Correct direction, nonliteral quote |
| Contract recovery rehearsal | unknown / missing | Wrong negative / none, nonliteral quote |
| Archive-region authorization | unknown / conflict | Correct two peer records and clarification request |

The prose mentions all four events and asks about rehearsal and authorization,
but its unknown rehearsal wording conflicts with its structured negative state.
The requested configuration action is not explicitly stated as a next step.
This review was performed by the assistant against frozen criteria, not by an
independent domain reviewer. Different-fixture separation cannot prove repair of
STRICT01, nor can one observation establish reliability or competitor parity.

## Bug analysis

### 1. Root cause category

- Cross-layer contract: a valid string type does not require a literal source span.
- Implicit assumption: a legal evidence branch does not establish that its passage
  proves the assigned direction. Missing records can still be mislabeled negative.
- Test coverage gap: controlled state truth tables validate legal combinations,
  not the model's interpretation of unrestricted evidence text.

### 2. Why earlier fixes did not finish the job

JSON mode did not enforce types; v18 made types explicit but left cross-field
combinations open. V19 closes those combinations, which this response respected.
Neither design constrains free quote transcription or proves source entailment.
The observed HTTP200/schema-valid response weakens endpoint-format rejection as
the explanation for this failure; the recorded invalid quotations and negative
classification directly establish the remaining failure modes. This does not
prove the endpoint always uses constrained decoding.

### 3. Prevention mechanisms

| Priority | Mechanism | State |
| --- | --- | --- |
| P0 | Preserve literal-source and semantic gates; retain failed original output | Done |
| P0 | Evaluate structured direction, prose and sources separately | Done for S1901 |
| P1 | Investigate selecting immutable offered evidence spans instead of regenerating quotes | Next design boundary; not implemented |
| P1 | Preserve missing-record, definite-negative and true-conflict semantic cases | Required before promotion; not proven by span selection |

### 4. Systematic expansion

The same transcription risk applies to every free-text definition/support quote.
Do not solve it with fuzzy matching, prefix removal or after-the-fact normalization.
An offered-span selection protocol could eliminate this transcription class while
leaving semantic evidence selection open; treat these as distinct acceptance gates.
Do not add routing/platform features or repeat failed public samples to hide this gap.

### 5. Knowledge capture

The quality spec and current task artifacts now record this distinction and the
failed observation. This repository has no `src/templates/markdown/spec` template
tree to synchronize. No runtime source, public schema or deployment was changed.

## Runtime and recovery evidence

Read-only pre/postflight verifies unchanged rc49 application source, DB0037,
v15 policy, module hashes and ledgers, five ready services and zero active jobs.
The conservative UTC2026-10-09 count is54/200:36 app dispatches plus18 known/reserved
direct requests, retaining the prior three unknown reservations. This slice made
one primary request and zero embedding calls; strict output stays disabled.

The initial freeze rejected non-ASCII synthetic filenames before criteria, plan,
intent or provider dispatch. Only filenames were corrected before freezing, and
the rejected artifact was retained. No request was retried.

All evidence is under the existing commercial recovery group,
`fallback-model-evidence/support-schema-live-*-20261010.json`, with the controller
`support_schema_live_probe_20261010.py`. Phase `support_schema_live_probe_20261010`
records baseline d74df4e, source/evidence hashes, pre-existing workspace preservation
and publication receipts. The original result SHA is
`22728d75ad5217fcbf7473db36bfcf859fd47ce746108aee4d1306b95de95b22`.
No disposable files or directories were created in this slice; diagnostic artifacts
are retained as deliverables. Existing workspace history and backups are untouched.
