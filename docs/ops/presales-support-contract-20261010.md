# Provider-visible prerequisite combinations, 2026-10-10

Strict output v19 now exposes the existing prerequisite evidence invariants to
provider decoding. It prevents the v18 failure's contradictory field combination
from being a valid candidate under the request schema. Default JSON mode remains
v15, strict mode remains disabled, and this implementation made no model or
embedding requests. No staging rollout or semantic acceptance is claimed.

## Contract change

The old strict schema only declared the types of positive, negative, unconfirmed
and uncertainty. It therefore accepted positive and negative together while
uncertainty was none; local business validation rejected that response afterward.

The new Pydantic union expresses four alternatives using native anyOf/$defs:

| Evidence combination | uncertainty | Required nonempty arrays | Required empty arrays |
| --- | --- | --- | --- |
| Positive | none | positive | negative, unconfirmed |
| Negative | none | negative | positive, unconfirmed |
| Missing | missing | none; unconfirmed may contain records of missing status | positive, negative |
| Conflicting | conflict | positive and negative | unconfirmed |

All alternatives retain the same proposition and literal definition fields and
existing list/text bounds. Top-level fields and public saved responses do not
change. The normal basis resolver still checks literal quotes, authorization-bound
references, questions for unknowns, status consistency, Chinese prose and final
size. No truncation, quote repair, type coercion or extra dispatch is introduced.

This addresses combinations, not meaning: a model may still merge unrelated
events, choose a wrong alternative or quote a passage that does not entail its
proposition. Neither the schema nor green tests prove independent atomic business
assessment. Endpoint compatibility with this larger anyOf schema is unverified.

## Identity and compatibility

Current strict requests use presales.v19, prompt SHA
`655d974ce22b099f135110c5be5fe0c766132960e48e39b41f38760095ea2249`.
The primary/fallback opt-in settings remain independent and false by default.
Frozen execution policy version/hash refuses v18-to-v19 substitution before a
request; accepted historical strict work must drain before a future rollout.
Default v15 prompt SHA remains unchanged.

The producer now emits run-v9. Both scorers explicitly bind each strict report's
recorded responseFormat to its version-specific schema and parser. Historical
StrictBasisDraft, strict_response_format and run-v8 interpretation are retained
byte-for-byte. Legacy v1..v6 scores and rejected run-v7 remain distinct. Failed
observations are never reinterpreted as new successful drafts.

## Evidence

The 24-case truth table compares both JSON Schema and Pydantic acceptance. Five
valid combinations are retained, including missing status with and without an
explicit unconfirmed quote. Source-bound projections preserve met/unmet/unknown
and legitimate conflict from separate source versions. Invalid quotes, foreign
citations, English-only text, absent confirmation questions and overflow still fail.

Four gateway/evaluator boundary tests failed against v18 before integration and
passed with v19. The combined new/boundary suite passed45 cases. Eighteen real
PostgreSQL cases passed for admission, synchronous/durable execution, replay,
accounting and protocol drift; fixture-owned schemas were removed in finally.
Whole-project checks pass: 3,074 nonintegration tests and 23 subtests (814 deselected,
263.39s), Ruff format/check for 745 files and Mypy for 276 source files. Exact-commit
CI, publication and owned-cleanup outcomes are recorded in the central phase receipts.

Offline inspection used the exact original v18 response. Its saved result and
score stayed unchanged; the new schema rejects prerequisites[0] at anyOf. This is
an offline schema observation, not a replay or proof that v19 generates a better
answer. Recovery and evidence use the existing commercial task group, phase
`prerequisite_support_schema_20261010`, baseline
`7a468d935fe76b4a2348edab0d87fa7455821577`. Git recovery paths and new-file absence
are registered; `support-schema-offline-evidence-20261010.json` contains original
hashes and identity checks. Historical workspace changes and failures are retained.

The competitor goal remains open: real model decomposition, complete answers,
independent user benefit and broader product acceptance are not established here.
