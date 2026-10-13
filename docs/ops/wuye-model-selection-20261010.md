# Wuye model comparison and staging replacement

Staging now runs signed **v0.1.45-rc.50**, application source
`a99b3760d7730f10bec1771867e090520e2a1018`, with **wuye / gpt-5.6-sol**
as primary. Grok4.7 remains the existing fallback. Schema0037, two-call row limit,
360-second row deadline,120/180-second route deadlines and200 daily dispatch limit
are unchanged. The user explicitly authorized this comparison and replacement.

This selects the most usable tested model on this channel and current product
interface. It does not establish general model superiority or production reliability.

## Comparison and original failures

The supplied suffix3 endpoint used path `//v1`. All initial nine requests received
HTTP200 with1166-byte HTML and failed the stream contract. A read-only `/models`
probe reproduced the HTML; correcting the same-origin path to `/v1` returned JSON.
The original nine outcomes are retained as routing failures, not semantic failures.
Only `FALLBACK_BASE_URL3` was corrected locally after a hash-verified private backup;
all other fields and credentials were preserved.

The unchanged v21 gateway then ran the three frozen cases once per available route:

| Model | Corrected-path observations | Decision |
| --- | --- | --- |
| gpt-5.6-sol | CQU02 upstream error at27.610s; CQU05 accepted at17.078s; QP01 accepted at57.062s | Selected for actual product qualification |
| claude-opus-5 | All three responses wrapped JSON in Markdown fences; rejected at15.625/17.235/24.671s | Not selected; no parser repair or relabeling |
| gpt-6-luna | Not advertised in `/models`; CQU02 returned503 at0.922s; remaining cases skipped | No usable output in this observation |

GPT preserves the separate10-year general experience and10-year software-project
management requirements, PMP, team7 and missing supplier evidence. Its CQU05 omits
the ancillary technical-lead5-year requirement, so completeness is not perfect.
QP01 preserves all six public states, distinguishes missing verification from
noncompletion, accepts completed training despite absent paperwork, refuses to borrow
another order's evidence, and describes conflicting authorization records. Its
internal authorization uncertainty subtype is `missing`, while the public answer and
row citations describe the conflict; retain this limitation.

Claude's CQU05 also reduces the dedicated software-project-management requirement to
generic experience in the substantive response. Its QP01 correctly represents the
conflict subtype but still fails the original wire contract. No additional prompting,
schema changes, resampling or new model survey was performed. Wuye prices and backend
model identity beyond returned model IDs are unverified.

## Actual product delivery

One two-row worksheet used actual local PostgreSQL, product API, hybrid retrieval,
background worker, remote inference, attributed review, reload and original XLSX/CSV
export. Authentication and source ingestion were fixture boundaries. Local hash
embeddings required **zero external embedding calls**.

- CQU02: GPT primary produced a usable draft in15.713s, preserving unknown supplier
  performance and treating test details as missing information rather than a new
  contractual prerequisite.
- CQU05: the primary returned a contradictory `unmet` assessment for absent supplier
  evidence and duplicate question IDs. The unchanged decoder rejected it. The normal
  bounded fallback then produced a Grok draft; the row completed in170.377s with two
  calls. This is retained as a failed primary output, not GPT semantic success.
- Both rows completed separate attributed review and XLSX/CSV delivery, with original
  drafts, attempts, citations, formulas and the unselected sheet preserved. Review
  clarified the dedicated project-management experience and technical-lead requirement.
- Three actual product calls, two successful rows and two successful usage consumptions
  were verified. The owned local database schema was closed, removed and verified.

Delivery artifacts in the central recovery group's `fallback-model-evidence`:
`wuye-workflow-reviewed-20261010.xlsx`, `wuye-workflow-audit-20261010.csv`, and
`wuye-workflow-delivered-20261010.json`. This is assisted public-sample delivery, not
independent business approval or a customer acceptance result.

## Executed release and verification

Exact-source [Quality](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/38043555857)
and [Container Supply Chain](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/38043555852)
passed. [Tagged rc50 release](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/38044856887)
published four signed images; five artifact digests and56 evidence files were verified.
The adapted import preservation boundary passed19 tests; primary switch/recovery
regression passed18 tests. Existing application validation remains3226 nonintegration
cases,23 subtests, Ruff/format and279 typed source files, plus the previously recorded
real PostgreSQL and affected wire tests. This turn changed no application source.

Full archive capacity, including transfer, normalization, unpack peak and eviction
reserve, passed before remote writes. Import retained all original image references;
no historical cache deletion was needed. A supervised `primary_model` window completed
in87.946s. It changed only the approved images, selected primary endpoint/name/key and
primary question-assessment flag. All fallback settings, resources and unrelated
configuration remain intact.

Independent postflight passed22 checks: exact deployment specs, credential binding,
272 packaged Python source files across core/API/worker packages, five ready services,
actual image identities, public assets, schema0037, all presales history hashes,
workbooks, manual/citation review history, accounting, idle state and capacity.

One direct request through the **installed API pod's actual gateway and settings**
then succeeded in11.559s, returned `gpt-5.6-sol`, used v21 and preserved both performance
requirements as unknown with the correct source quotation. This smoke check supplied
the fixed public evidence directly: zero embeddings and zero application business
writes. It is not a second live HTTP questionnaire workflow or a reliability claim.

The round dispatched20 HTTP generation requests:9 wrong-path,7 corrected comparison,
3 local-product and1 deployed smoke. These are request counts, not proof that every
request reached model computation or was billed. The conservative carried reservation
ceiling is68/200; original errors and unknown usage remain visible.

## Recovery and remaining scope

Use the ongoing recovery group
`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness`,
phases `wuye_model_selection_20261010`, `application_rc50_20261010` and
`rc50_image_windows_20261010`. Original provider configuration is a verified local
private snapshot; original/candidate deployment templates and credential digests are
bound to the release plan. Keep signed rc49 images and the original Grok primary key
and configuration for recovery. Both versions read existing0037 history; no schema
downgrade is required. Recovery was covered by controlled tests; an extra disruptive
live rollback/reapply cycle was not part of this model replacement.

Cleanup removed only this round's local OCI transport directory, the two terminal
remote import/release runtime directories (including protected key inputs), and the
owned local test schema. Exact temporary inventories and file hashes were checked;
all old/new image references remain. The first cleanup stopped before deletion on
an overly narrow directory-shape assertion; read-only inspection identified the
task-created kubectl cache directories and a separately recorded exact cleanup
completed. Final readback confirms the new policy, zero active jobs and unchanged
historical accounting. Central evidence and recovery files remain retained.

Review and existing bounded fallback remain necessary: the selected primary produced
both an upstream error and a rejected semantic/structural answer in this small sample.
Do not present the switch as perfect model acceptance. The task and PR remain open;
real-user usefulness, time savings and willingness to pay still need actual users.
No product/spec contract changed in this continuation.
