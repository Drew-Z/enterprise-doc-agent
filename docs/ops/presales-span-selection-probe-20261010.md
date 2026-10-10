# V20 source selection and meaning observation, 2026-10-10

The first v20 request produced an accepted draft with exact offered source text,
but failed the separately frozen semantic criteria. Strict mode remains ineligible
for staging enablement. Preserve the original accepted transport/decoder outcome
and failed semantic review as different results; neither overwrites the other.

## Frozen scope and observed result

S2001 is a new assistant-authored synthetic order, not a public procurement task.
It asks whether 180-day monthly-report backup can start and requests the state and
next action for five separate prerequisites. Five sources supply necessity rules,
mixed current-state records, contradictory peer authorization records and a
different order's completed records. Criteria were frozen separately before the
request and were never sent to the model. No earlier sample was rerun.

Candidate source: `c3ab10d89ba81eb38641c9b479a61991bcccba92`, presales.v20,
prompt SHA `b2edcfdc8b9f9296f629ace9bf9255b6e7f9b34e86b787ef392cdc265b517375`.
The unchanged primary grok-4.7 route used low reasoning, streaming and its existing
120-second timeout. Exactly one request completed in 52.016 seconds, HTTP 200;
the provider reported 5,928 input and 3,202 completion tokens, 9,130 total.
Price and billed cost remain unverified.

JSON Schema, all selected span IDs, the production decoder and source-bound
mechanical scorer pass. The top-level conflicting_evidence status and four cited
sources match the reference. These checks do not inspect necessity or entailment.

| Business event | Frozen expectation | Original structured result | Definition selection |
| --- | --- | --- | --- |
| Enterprise backup licence | met | met | Wrong: current-state record |
| 180-day retention configuration | unmet | unmet | Wrong: current-state record |
| Report recovery verification | unknown / missing | unmet / none | Wrong: current-state record |
| East China storage authorization | unknown / conflict | unknown / conflict | Correct necessity rule |
| Administrator safety training | met despite absent certificate | met | Wrong: current-state record |

The exact source text `报表恢复校验结果未登记；` was selected as negative evidence.
Its absence of a registered result does not establish failure to pass verification.
The prose correctly says the result is unregistered, contradicting its structured
unmet state. Four definitions select current facts instead of the offered rules
establishing why those events are prerequisites. A top-level rule citation does
not fix the meaning of those four separate definition selections.

The answer lists all five statuses but supplies only authorization clarification
as an explicit next action. It omits configuring retention and checking the actual
verification result. The generated conditions name target states; they do not
provide the requested actions. Training stays met, no certificate-submission duty
is invented, and the other order's evidence is not used to fill the current gap.

The assistant reviewed all nine frozen criteria; criteria 3, 4, 8 and 9 fail.
This is not an independent domain review. One source-selection success does not
establish historical repair, a reliability rate, public-task improvement or
competitor parity. There is no retry, output repair or semantic reclassification.

## Decision and next boundary

Source-span selection handles transcription in this observation, while evidence
role, missing-vs-negative reasoning and requested-action completeness remain open.
Do not enable strict staging mode on the strength of acceptedDraft, statusMatch
or citation coverage alone. Keep the literal decoder and original failed semantic
review. Do not add another instruction-only probe, change models, simplify the
reference or classify arbitrary source meaning with keyword rules to turn this
result into acceptance. The next design must address evidence roles and per-item
answer/action coverage, then prove its boundaries before another real observation.

## Runtime and recovery

Fresh read-only pre/postflight confirms unchanged rc49 / source fe99567, DB 0037,
default v15 policy and module hashes, five ready workloads, zero active jobs and
unchanged application ledgers. No deployment, configuration or embedding change.
The current UTC day is 2026-10-10: zero application dispatches and this one new
direct request were observed. Budget accounting conservatively carries all 18
previous-day known/unknown reservations, producing a 19/200 ceiling; this is not
a claim of nineteen observed calls on the new day.

The existing recovery group records phase `span_selection_live_probe_20261010`,
Git baseline c3ab10d and six registered documentation paths. The controller reuses
the existing preflight, credential loader, collector and scorer, with exclusive
input/criteria/plan/intent files and no repeated dispatch. Controller syntax/import
and source/schema bindings were checked; unchanged product source retains its
previous successful CI. Documentation validation and publication receipts are
recorded centrally. No disposable directories were created in this observation.

Evidence is retained under the commercial recovery group's fallback-model-evidence:
`span-selection-live-{input,criteria,plan,intent,result,score,review,postflight}-20261010.json`
and `span_selection_live_probe_20261010.py`. Original result SHA:
`ebab2f645c31cb4c28b0aa9a43d6f9979085d8974fc8ad74879341306405e74a`.
Prior v18/v19 failed evidence, frozen public samples and unrelated workspace files
remain unchanged. The parent competitor-standard goal remains active.
