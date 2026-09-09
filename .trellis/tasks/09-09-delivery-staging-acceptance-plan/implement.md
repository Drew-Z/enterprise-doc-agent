# Delivery and staging acceptance plan: implementation

## Authorization and dependencies

The user's continuous Trellis request authorizes local analysis and plan artifacts.
Both previous children have passed local validation. Commit and external actions retain
the concrete approval gates in `.trellis/workflow.md` and the workspace AGENTS.md.

## Ordered execution

- [x] Review final local validation, original archive integrity, canonical checkout and worktrees.
- [x] Inspect the actual deployment/supply-chain workflows, logging/smoke specs and prior deployment plan.
- [x] Write exact commit batches and the future staging acceptance procedure.
- [x] Validate complete dirty-path coverage, contexts, local links, JSON, hashes and diff state.
- [x] Update parent and previous-child transitions; record this local plan in review.
- [ ] Present the single concrete local commit confirmation required by Trellis Phase 3.4.

## Owned paths

This task directory, the continuous parent task records, and the M6 child link generated
by task creation. No source, existing evidence, workflow, worktree or external system
is changed by this planning child.

## Checks

Record commands and observed results in `validation.json`. Reuse previous code gates only
after matching their source hashes; inspect package quality contexts. Validate the
commit-manifest path union against `git status --porcelain --untracked-files=all`,
the 86 inherited file hashes, both child validation source hashes, exact Git HEAD/index
and task context entries. Check local Markdown links and JSON/JSONL parseability.

The first record validator correctly covered all 128 paths but overgeneralized `.json`
extensions to four historical mixed command captures. Primary inspection confirmed three
warning-prefixed JSON payloads (collection exit 0) and the first run's warning-prefixed
NotFound capture (exit 1). The corrected check preserves their baseline bytes, validates
those exact capture contracts and still parses every authored JSON/JSONL record strictly.

## Transition

After local plan validation, leave this child in `review` and the parent active at the
delivery-confirmation gate. The next action is the exact approved local commit batch;
future candidate/version selection and staging preflight follow only when their prerequisites
exist. Formal task archives and journal commits follow work commits under the existing
workflow. None are represented as completed by this planning record.

The local plan now passes exact coverage for 128 files in four work commits, with no
unknown dirty paths. The 86 inherited archive files and all 12 tested source/spec files
remain unchanged; HEAD/index are unchanged and staging is empty. Context, JSON/JSONL,
Markdown-link and diff checks pass. This child is in review; commit confirmation remains
the next action. No new runtime tests were needed for this documentation-only child.
