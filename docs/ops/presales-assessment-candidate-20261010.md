# Rule, assessment and response candidate, 2026-10-10

A standalone candidate now separates selected necessity rules, one assessment per
rule and an answer for each exact portion of the requirement. Outstanding items
must carry their own action or confirmation question; completed items cannot add
an assessment action. This candidate is not connected to the production gateway.
Default v15 and optional v20 remain byte-identical, and no new provider, embedding
or staging operation occurs in this implementation phase.

## Problem and design

The original S2001 result remains mechanically accepted and semantically rejected.
It selected exact source spans but used current-state facts as four necessity
definitions, labeled an unregistered result negative and omitted two next actions.
Earlier schema and literal-quote constraints address different failure classes;
they cannot prove source meaning. Adding another instruction to the existing
free answer would not structurally retain a separate action for every item.

`assessment_selection.py` introduces private protocol
`presales.assessment-candidate.v1` with three explicitly linked collections:

- `rules`: an affirmative business proposition and `requiredBy` source-span IDs.
- `assessments`: every zero-based ruleIndex exactly once, with met, unmet, missing
  or conflicting evidence, a short current-state summary and its own nextAction.
- `responses`: ordered exact requirementText portions, each with an answer and
  selected citation IDs or a specific information gap.

The strict schema has mutually exclusive assessment variants. Met requires evidence
and a null nextAction. Unmet requires contrary evidence and a completion action.
Missing allows absent observations but requires a confirmation question. Conflict
requires both source sides and a reconciliation question. No additional inference
request, route, keyword truth classifier or after-generation repair is introduced.

`resolve_assessment(content, requirement, catalog)` rejects missing/duplicate/foreign
rule links and changed, omitted, reordered or repeated requirement text. Only
whitespace gaps are permitted. It renders all per-question answers and gaps, then
each state summary/action, into the unchanged public answer field. Unknown/conflict
questions also remain in missingInformation. Each raw prose field passes the
existing Chinese-language guard before labels are added. Final size overflow
rejects without truncation. Existing v20 span resolution enforces source identity
and inherited business validators; prior interpreters are untouched.

The private prompt/schema SHA is
`f274eeba7c7f7c32b7420c2c2d0f37246f170bde0504f496b0f3bf8677f13b07`.
This identifies the unpromoted candidate, not a deployed model policy. Larger or
smaller text size alone does not establish provider compatibility, latency or cost.

## Validation boundary

Forty-one candidate cases and 45 existing span/evaluator cases pass together.
Red evidence records the absent candidate API and a subsequent real projection
failure: a per-question information gap survived only in the global list, losing
its question placement. The projection now keeps that gap with its question and
in the global list. Tests cover four states, strict required properties, links,
exact coverage, source authorization, raw-language enforcement, public size limits,
legitimate combined rule/state passages and unchanged gateway prompt identities.

Full regression passes 3,158 nonintegration tests and 23 subtests (814 deselected,
277.32 seconds). Ruff format/check covers 749 files and Mypy passes 278 source
files. No database or policy execution path changed; no new database integration
claim is made. Exact-commit CI/publication receipts are recorded centrally.

A controlled counterexample deliberately assigns an unregistered result to unmet
and uses a state fact as a rule. The parser retains both model choices rather than
pretending to correct meaning. Separate fields improve inspectability; they do not
make an arbitrary selection semantically correct. Exact question coverage also
does not prove useful decomposition or a substantive answer to every aspect.

The next gate is a separately frozen actual candidate observation with full role,
state, scope, answer and action criteria. Do not integrate gateway/scorer/policy
changes on local schema tests alone, rerun historical failures, simplify criteria
or count this implementation as competitor acceptance. Actual endpoint behavior,
semantic improvement and public-task quality remain unproven.

## Recovery

The existing commercial recovery group records phase
`presales_assessment_candidate_20261010`, baseline
`adb07d6e964af780e088ab5b0877448b266e0b3d` and eight registered paths. New files are
recorded absent at baseline; tracked unchanged files have verified Git recovery.
`assessment-candidate-offline-evidence-20261010.json` verifies ten unchanged runtime
sources and the three original v18/v19/v20 result hashes. Full project checks,
publication and owned temporary-directory cleanup are recorded centrally. No
historical files or unrelated workspace changes are part of this candidate.
