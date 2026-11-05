# Immutable evidence selection, 2026-10-10

Strict presales output now selects offered source spans instead of asking the
model to regenerate quotations. This addresses the transcription failure observed
in S1901: a quote cannot silently acquire a shared date/order prefix. The model
returns only a span ID; the server retrieves its exact pre-offered substring.
Old free-text quote objects and unknown IDs are rejected, not repaired.

This implementation makes zero live model or embedding requests and does not
deploy or enable strict mode on staging. Semantic improvement remains unproven.

## Input and output boundary

Reuse the existing per-call citation catalog and source authorization. Every
excerpt offers its complete text and exact punctuation-delimited substrings;
only surrounding whitespace is discarded and repeated identical substrings are
deduplicated within that excerpt. All complete evidence/source context remains
visible, including subjects, versions, exceptions and applicability. These are
lexical spans, not a claim that punctuation identifies independent business events.

The output keeps four mutually exclusive support combinations, but definition,
positive, negative and unconfirmed each contain only `{spanId}` references.
Materialize selections into the same parent citation IDs and exact quote text,
then run the unchanged v19 and basis business validators. Public saved drafts,
conditions, original citation snapshots, review and workbook/CSV formats do not
change. No retrieval, routing, quota, retry or database migration is introduced.

Each request derives its span IDs from its own random citation prefix. No mutable
catalog lives on the gateway; a response from another simultaneous request cannot
reuse its IDs. Expanded inputs remain subject to the existing128KiB request limit
before any provider dispatch. Offering spans adds input bytes; no latency/token
saving or endpoint compatibility claim is made without a later actual observation.

## Semantic boundary

The mapper does not change the model's proposition, evidence direction, uncertainty
or prose. A real span saying "status unregistered" can still be selected as negative
evidence, which is semantically wrong. A controlled case makes that remaining
boundary explicit. Selecting authorized text proves origin; it does not prove
that the text entails the conclusion. Independent missing-vs-negative, scope,
decomposition and answer-completeness checks are still required before promotion.

## Identity and historical records

The new opt-in mode is presales.v20, prompt SHA
`b2edcfdc8b9f9296f629ace9bf9255b6e7f9b34e86b787ef392cdc265b517375`.
Default v15 prompt bytes/hash and both default-false route settings are unchanged.
Accepted v18/v19 policies cannot restore through a v20 template; drain old accepted
work before any future release. Future span-algorithm changes also require a new
protocol identity and preserved historical interpretation.

The collector emits run-v10. Both scorers verify the recorded schema and recreate
the entire offered span list from the bound source input. Changed text, IDs,
parent sources, order, duplicated or omitted spans reject even for failed reports.
Accepted responses use the same core selection decoder. Historical v8/v9 retain
their byte-identical schema modules and original resolvers; older reports remain
unchanged. Offline rescoring confirms both saved live failures have identical
scores, and old v19 quote objects are not accepted as new span selections.

## Validation and recovery

Red/green checks establish the offering and projection boundaries, then an actual
HTTP-boundary test fails against the old gateway's absent spans and passes after
integration. Two evaluation cases fail against the old producer before run-v10
integration. The focused suite passes54 tests, including42 new module tests and
all24 legal/illegal field combinations. Eighteen actual owned PostgreSQL cases pass
in40.85s; selected literal evidence persists through synchronous and restored
background execution with idempotent replay and one charge. Fixture-owned schemas
are removed in finally.

The first database run hit two fixture setup failures from a duplicate generation
chunk index, before inference; the fixture now creates its own document/generation.
Initial lint findings were escaped Chinese punctuation and line length, corrected
without changing runtime text. Ruff checks747 files; Mypy passes277 source files.
Full regression passes3,117 nonintegration tests and23 subtests (814 deselected,
268.00s). Exact-commit CI and cleanup outcomes are recorded centrally.

Recovery uses the existing commercial group, phase `presales_span_selection_20261010`,
Git baseline `8dc0b3154a39e2e88794bb04f2868a2297ec116e`, with16 registered paths and
new-file absence. `span-selection-offline-evidence-20261010.json` retains immutable
failure hashes and schema/default-identity checks. The2,993 unrelated workspace
entries are preserved. Competitor acceptance, actual endpoint behavior and model
semantic quality remain open.
