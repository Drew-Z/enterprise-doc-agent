# Separate answer protocol: rejected candidate, 2026-10-10

The private v17 answer-aspect protocol failed its single frozen CQU04 probe. It is
not integrated into the gateway or deployed. Runtime remains v15. The candidate
demonstrated deterministic text-coverage checks, but did not demonstrate better
answer completeness or reliable adherence to its JSON schema.

## Implemented and checked boundary

The private CoverageDraft held ordered answers (exact requirementText, answer,
citations and missingInformation) separately from source-defined prerequisites.
The resolver required complete ordered question coverage, allowed only whitespace
between spans, required evidence or a gap for each item, and projected into the
unchanged literal-basis decoder. It retained public size, Chinese prose, citation
and source-quote checks. It did not prove semantic decomposition or entailment.

Thirteen local tests passed after a real missing-module red and an evidence-or-gap
red. Tests covered omissions, repetition, reordering, invented text, punctuation,
foreign/duplicate citations, English-only prose, public-size overflow and separate
business prerequisites with literal definitions. Ruff format/check passed after
fixing the initial long import and four fullwidth-punctuation warnings. These tests
are evidence about the parser, not evidence of model quality.

The exact module and tests are preserved with SHA-256 verification in the existing
central recovery group under `files/answer_coverage_protocol_20261010/`. Their two
new, untracked candidate files were then removed from the canonical checkout. No
rejected runtime module, compatibility branch or test-only feature is shipped.

## Frozen first outcome

One previously unexecuted CQU04 question asked for the coverage and implementation
evidence of sensitive-data storage encryption and masked display. Its only source
was the exact archived procurement sentence, "系统对敏感数据要加密存储和脱敏展示。"
No supplier implementation evidence was supplied. The input, independent-of-input
reference criteria, source hashes, prompt/schema and current route/budget were
frozen before dispatch. The criteria were authored by the assistant, not approved
by an independent domain reviewer.

The existing primary route (grok-4.7, low, streaming, 120-second timeout) made one
request. It returned HTTP 200 in 9.390 seconds, but the gateway rejected the draft:
`draft_schema`, `list_type` at `answers[0].missingInformation`. The model returned
a string where the schema requires an array. No normalization or second call was
used to convert the original failed observation into a success.

Inspection of the rejected raw prose also fails the frozen semantic criteria:

| Criterion | Observation in the original rejected text |
| --- | --- |
| Treat storage encryption and display masking separately | One generic evidence-shortage sentence |
| Ask for field/storage and page/role coverage | Only generic coverage requested |
| Ask for encryption configuration/key handling and masking rules/display/test evidence | Only generic implementation methods/documents requested |
| Avoid inventing a verification prerequisite | Raw prerequisites is empty |
| Preserve the exact question | One item copies the entire question; this does not prove a complete answer |
| Avoid claiming supplier completion/noncompletion | No unsupported implementation claim observed |

Reported usage: 2,955 prompt tokens, 953 completion tokens, 3,908 total. Monetary
cost is unknown without verified pricing. Original result SHA-256:
`1c95dc7c4536442affcb6927c27ae1105dbccd0372415fbf74aa10e1f5fb2b0c`.
Candidate prompt SHA-256:
`e0e0c66cb7a56d1eb6896f0f6fbe9861ef89a52ad8094e117a3ce199f79c9e31`.

The report is explicitly candidate-only run-v7. Existing v1..v6 scorers are not
changed to accept it, and no historical outcome is reinterpreted. The isolated
probe temporarily bound one immutable question in its dedicated process; that
harness is not a proposed shared runtime gateway implementation.

## Decision and current product boundary

Reject promotion. Preserve the failed result, criteria, executed source, review
and pre/post observations as `coverage-v17-cqu04-*` in the existing central
`fallback-model-evidence` directory, with controller
`coverage_v17_cqu04_20261010.py`. Offline reproduction requires the preserved
candidate module; the live controller must not be run again.

The result does not justify another paid prompt variant or a wider model/channel
search. Before a future live plan, an offline design must address both provider
schema adherence and substantive per-aspect answers; text copying alone is not
an acceptance criterion. Existing human response, evidence correction, review and
original-workbook delivery remain the available completion path. No independent
user benefit or competitor parity has been demonstrated.

Read-only pre/post checks confirmed rc49 / schema 0037 / v15, five ready workloads,
zero active jobs and unchanged route policy/application ledgers. Conservative UTC
2026-10-09 usage is 52/200: 36 app calls plus 16 extra known/reserved direct calls,
including three prior unknowns. This is a budget reservation, not an exact billed
call count. No embeddings, tenant writes, packet edits, deployment or cache cleanup
occurred. CQU01/CQU03/SWU and prior unknown requests were not rerun.
