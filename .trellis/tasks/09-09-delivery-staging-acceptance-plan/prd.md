# Delivery and staging acceptance plan

## Goal

Make the validated smoke-report and consumer-attribution changes reviewable for local
delivery, and define the evidence required before a separately authorized staging attempt.
Implements a bounded part of M6-R4 and parent DR-10.

## Confirmed Facts

- The canonical main checkout remains at `ffca3d0861ae22c857fef54260b9fc4c083e980e`.
- Both previous continuous children passed local validation and remain in review pending commit.
- The 86-file historical failure archive is a separately identified inherited change set;
  all original bytes and the real Git index were verified unchanged on 2026-09-09.
- The latest local backend gates passed 1086 non-integration tests, 22 related local
  integration tests, Ruff and strict mypy. They do not establish staging acceptance.
- The existing deploy workflow executes smoke code from its exact workflow ref and uses
  four supplied image digests. The old v0.1.34 tag cannot exercise these new uncommitted fixes.
- Historical deployment and trial permissions were consumed by their recorded runs.
  Trellis Phase 3.4 requires one concrete commit confirmation; push and release actions
  retain separate authorization.

## Requirements

- **DSA-R1**: Classify every dirty file into coherent, ordered proposed commits with
  explicit file lists; preserve the inherited archive batch and list unknown paths separately.
- **DSA-R2**: Bind the plan to current HEAD, existing validation and source hashes. Verify
  exact file coverage, parseable records, local links, task contexts, unchanged prior
  evidence/source identities, empty staging and whitespace before presenting confirmation.
- **DSA-R3**: Define future source/tag/image, supply-chain, live-state, prerequisite,
  credential, reindex and deployment gates from actual workflows. Unknown future values
  remain explicit prerequisites rather than invented version/digest/approval values.
- **DSA-R4**: Define both success and failure collection for schema-v2 smoke and runtime
  attribution. Distinguish local adapter proof, actual broker execution, durable settlement,
  historical diagnosis and release acceptance.
- **DSA-R5**: Preserve the continuous parent queue and next actionable step. Commit, formal
  archive, journal bookkeeping, push, release build, deployment and provider trials must be
  recorded according to their actual execution state.

## Acceptance Criteria

- [x] Every dirty path appears exactly once in the proposed commits, with unknown paths listed.
- [x] Exact commands, validation references, local checks and preservation boundaries are recorded.
- [x] Staging prerequisites, side effects, acceptance/failure collection and stop conditions are reviewable.
- [x] Parent/current-child state points to the concrete next step without closing external milestone gates.

## Out Of Scope

Executing commits or remote mutations before confirmation, choosing a new release version
without a candidate review, changing workflows/runtime code, rerunning historical trials,
or treating Ready workloads and local tests as an accepted staging release.
