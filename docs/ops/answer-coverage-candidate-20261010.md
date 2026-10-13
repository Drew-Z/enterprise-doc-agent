# Answer coverage candidate: rejected, 2026-10-10

The v16 instruction candidate did not meet its frozen semantic criteria and is not a
release candidate. The production instruction remains v15. More explicit field
descriptions alone did not resolve the distinction between a buyer's evaluation request
and the supplier's actual business obligation. Do not report this as improved accuracy.

## Hypothesis and bounded change

The original v15 CQU03 response omitted testing arrangements and confused a known
necessity rule with an unknown completion event. The candidate preserved every existing
field, validator, citation constraint and projection, but clarified proposition versus
definition and asked the answer to cover all aspects of a requirement. It added no
route, retry, inference stage or model. Candidate prompt SHA:
`f7cbef2d2369ccf33bf61f8914e393d5921a849b26ae00336a8253750b39ee2f`.

No CQU03, SWU or previous unknown request was rerun. One previously unexecuted CQU01
requirement was frozen with a separate reference before its first request. It uses the
[historical CQU procurement clause](https://ztbzx.cqu.edu.cn/sfw_cms/r/s/cms/76c7edb7203c404d8ebc49a4f60ebf6c/0)
about Co-op functionality/data integration and second-level synchronization **or** a
unified data source. The supplied source is a procurement requirement, not a supplier
completion record. This is a single-source generation probe, not product retrieval,
a controlled v15/v16 comparison, independent review or customer validation.

## Original result and decision

The unchanged primary route (grok-4.7, low, streaming,120-second deadline) returned one
schema-valid draft in27.625s. It correctly chose insufficient_evidence, kept the OR
relationship and cited both required anchors. Reported usage was2,814 prompt and1,755
completion tokens; currency cost remains unknown. No second request was made.

The mechanical scorer passed, but the pre-frozen semantic criteria did not:

| Criterion | Original observation |
| --- | --- |
| Function/data integration and data-path coverage | Named all in the answer |
| No unsupported supplier completion claim | Passed; current status stayed unknown |
| Concrete interface/data-model or integration-plan inquiry | Omitted; asks only for verification proof/results/configuration evidence |
| Business event versus reviewer instruction | Failed: proposition says the system has completed the **verification** of integration, while the cited clause requires integration itself |
| Avoid generic proof-shortage response | Incomplete: largely restates the question and says verification proof is missing |

The new proposition is “系统已完成与现有Co-op实习管理系统的功能和数据整合、秒级同步或统一数据源的核实”.
The procurement source never imposes that verification activity as a separate business
prerequisite. Its literal quote is valid, but does not establish the proposition selected.
This is the same class of semantic boundary problem under a different question, not a
schema/transport failure and not proof of the specific old SWU failure cause.

Decision: reject v16, retain the original run and restore the two runtime source files
exactly to Git d95475e. Preserve the exact executed candidate in the existing central
recovery group before restoring. Do not deploy, loosen validators, increase reasoning,
switch channels or continue sampling under this one-request plan.

## Next implementation boundary

Stop adding instructions to the existing prerequisite-first response as the next move.
The next design must distinguish **requested answer aspects** (what the customer asks
the assistant to assess) from **business prerequisites** (what the sources actually
require the supplier to do). Coverage needs an explicit representation and reviewable
projection; “核实/说明” in a question must not silently become a supplier completion
obligation. A coverage item can be unknown without manufacturing a new prerequisite.
Choose the smallest model-facing/public-review contract that expresses that distinction
before another paid request; preserve existing drafts, evidence and reader compatibility.
Semantic enforcement must remain an explicit limitation: structural coverage alone
cannot prove entailment or factual correctness.

## Evidence and operational boundary

Input SHA: `6a18a0036ba74e98abf6debea3351f08fe67989c25c880ef80ceef8df2105f4c`.
Original result SHA: `1f335fdab0f55e8a0bc69387e21f5b7c754310571d588bd5b37c246d28c954c2`.
The original capture and sample-pack hashes remain unchanged. Publication timestamp
uses retained capture metadata; no new web fetch was performed. Reference and assistant
review are separate; no independent domain approval is claimed.

Read-only pre/post observations confirm rc49/0037, v15, unchanged primary/fallback policy,
five ready workloads, no active jobs and unchanged application ledgers. One new direct
request increases the conservative budget to51/200:36 app +14 previous known/reserved
direct calls +1 new. Three prior unknown calls remain reserved. No embeddings, tenant
writes, frozen packet changes or staging deployment occurred.

The candidate passed174 focused protocol/scorer tests, Mypy274 source checks and the
full3,029 nonintegration tests/23 subtests (810 deselected,262.29s). These
checks do not test semantics. Its initial Ruff check also found17 fullwidth-punctuation
warnings in the new Python strings. Preserve that failed check; do not fix and promote
a semantically rejected candidate. Final validation and restoration receipts live in
the central `answer_coverage_candidate_20261010` recovery phase alongside the exact
executed source snapshots and `coverage-v16-cqu01-*` evidence files.
