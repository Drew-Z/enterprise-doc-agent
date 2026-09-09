# Delivery and staging acceptance plan: design

## Delivery units

Use four ordered work commits: historical failure archive; smoke report source/tests/spec
and its local task record; consumer attribution source/tests/spec and its local task
record; continuous-parent links and this delivery plan. Task history is retained in the
active tree. Formal archive and journal bookkeeping follow committed work in the existing
Trellis workflow and are not silently performed while source remains uncommitted.

`commit-manifest.json` enumerates exact repository-relative paths and commit messages.
Its path set includes its own record and this task's validation, avoiding a wildcard
staging command. Hashes for source and the inherited archive come from the validated
snapshots; the final plan validator binds this manifest and the other plan documents.

## Future release gates

The user reviews local commits first. A subsequent published candidate must have an
explicit tag and final source SHA, successful Quality checks and a strict four-image
supply-chain manifest. Only then can exact live before/after prerequisite values and
deployment inputs be reviewed. The current plan intentionally does not authorize
unresolved future registry, credential or cluster mutations.

The existing `single-node-4c4g` workflow temporarily stops workloads during migration
and scales consumers during embedding reindex. Review these real actions and potentially
billable smoke calls together in the future concrete deployment plan. A new run is one
attempt; failures stop for evidence and diagnosis.

## Attribution and evidence

Require the workflow's schema-v2 smoke report and match its hashed run reference through
read-only durable execution/attempt data to the actual consumer's configuration/claim
events and process/image identity. On natural handler failure, collect the safe handler
and durable failure events when that path ran. Missing events remain missing evidence,
not proof of another worker or a reason to reclassify a failed release as passed.

The collector currently reads at most 500 lines per current/previous container. Plan
timely, bounded collection by actual Pod UID and smoke time window when that tail is
insufficient; preserve the collection result and sanitize before repository storage.
No live fault injection or extra QA is implied by this plan.

## Validation and limits

This child changes task/plan records only. Reuse already passed code gates while source
hashes match. Validate JSON/JSONL, local Markdown targets, exact dirty-file coverage,
source/archive hashes and Git index/diff state. Four inherited workflow capture files
use a `.json` name but contain merged K3s warning lines and stdout; the first failed run's
embedding capture also contains a NotFound result. Preserve their original bytes, verify
their corresponding collection-status exit codes and inspect payload structure separately.
They are raw command captures, not authored JSON records to rewrite for a parser.
Do not add implementation-mirroring tests
for a documentation-only child. No additional adopted runtime spec is needed: the two
previous children already recorded the smoke and logging contracts.
