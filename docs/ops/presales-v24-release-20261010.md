# v24 compatible publication

Signed rc51 at `3b44a56778841d5c7b5da04abec0f85d1faf1d91` is deployed on staging with
v24 and schema0037. CQU06, CQU05 and QP01 completed normal generation, attributed
review, reload and original XLSX/CSV delivery. Switching to v21 on the same compatible
images preserved the records and byte-identical exports; v24 was then restored.

## Recovery design

One signed build carries the expanded API/Web readers and two explicit generation
versions. `PRESALES__QUESTION_PROMPT_VERSION=presales.v21` preserves the original
system message, version/hash and projection; v24 uses the clause/numeric instructions
and separate information gaps. Default is v24. Frozen execution-policy restoration
retains the selected version and refuses cross-version work before provider dispatch.

First deploy this build with explicit v21, preserving existing model routes, keys,
budgets, schema0037 and all history. Verify it independently. A second drained release
window changes only the prompt setting to v24. Its rollback restores v21 while keeping
the expanded readers. It does not restore the old rc50 reader or a database snapshot.

Schema0037 release plans require `missing_information_readers` alongside existing
workbook/manual/citation capabilities. The guard checks every draft and every review
revision before mutation and after admission closes. An extended array blocks an
incompatible candidate/recovery reader, including when the latest review is shorter.
No historical record is removed to enable rollback.

## Implementation verification

New controlled tests establish exact prompt identity, original v21 rejection behavior,
unchanged single-dispatch semantics, invalid-version refusal and history/idle races.
The real PostgreSQL workflow generates14 gaps, switches to the v21 recovery setting,
and reads, reviews, reloads and exports them through XLSX/CSV. The actual guard SQL
also detects an old extended review when both current draft and newest review are short.
All database edits in this check belong to isolated fixtures, removed in finally.

Local checks:3377 nonintegration cases and23 subtests pass; one additional case failed
only because a child test process inherited Windows GBK, then passes with explicit
Python UTF-8 mode (3378 total verified). Four real PostgreSQL workflow cases pass.
Ruff,759-file format and mypy279 pass. The initial unbounded directory collection was
stopped before test execution and replaced with351 explicit project test paths to avoid
walking ignored dependency trees. Implementation made no provider calls. The separately
authorized live publication and finite delivery results are recorded below.

## Fixed live delivery boundary

Run CQU06, CQU05 and QP01 once through normal generation with existing bounded fallback,
reserving at most six calls on top of the conservative77 prior reservations. Keep raw
outcomes, evidence, attributed manual revisions, reload results and XLSX/CSV separately.
CQU06/CQU05 share one public procurement source; QP01 is synthetic. These checks do not
prove real customer value, universal model quality or commercial acceptance.

Recovery and private operational evidence stay under the existing central task group
`D:/Agent/codex/backups/tasks/20260924T015840.986Z-commercial-production-readiness`, phase
`semantic_release_v24_20261010`. The Git baseline covers all original tracked files;
new paths are recorded absent. Retain the existing2993 unrelated workspace entries.

## Published identity and live windows — 2026-10-11 Asia/Shanghai

Exact application-source Quality run `38053083937`, PR supply-chain run `38053083907`
and tagged supply-chain run `38069840277` passed. Tag `v0.1.45-rc.51` binds the application
commit above. Five release artifacts and56 evidence files were checked; successful
exact-source CI supplies cryptographic signature/attestation verification, with local
subject/digest review. The deployed Web contains `tiff-4.7.2-r0`.

The specifically approved32 rc38/rc39 containerd references were removed, freeing
2,212,421,632 bytes. All40 protected references remained. The unchanged aggregate space
guard then passed and all four rc51 images imported without altering unselected refs.

Compatible-reader publication pinned v21 first; a separate window activated v24.
Later windows restored v21 with saved22/14-item histories and reapplied v24. Independent
readbacks checked all five workload specs/readiness, image/source identity, credentials,
schema, queues, public assets, capacity, budgets and full presales history hashes.
Rollback took83.297s and reapply82.818s; all22 checks passed in both final windows.

The first v24 activation's strict receipt failed only the historical-terminal-Pod
preservation check:30 old terminal Pods and their four owner ReplicaSets disappeared.
Each affected Deployment had `revisionHistoryLimit=10` and retained ten old ReplicaSets
plus one current. This supports existing controller GC as the cause, but deletion audit
records were unavailable, so actor attribution is inferred. No explicit Pod/ReplicaSet
deletion was issued by the release controllers. The original failed receipt remains
immutable; a separately hashed `passed_with_runtime_gc_exception` receipt names exactly
those30 UIDs and passes every business/runtime check. Later windows used a fresh baseline
and required normal terminal-Pod preservation; neither inherited that exception.

## Fixed delivery outcome

| Case | Original generation | Frozen source review by Codex | Retained missing-information items |
|---|---|---|---:|
| CQU06 | Primary succeeded, one call | All seven service clauses; precise1h/4h meanings; supplier state unknown |22|
| CQU05 | Primary succeeded, one call | PMP, separate experience categories, inclusive thresholds, technical lead and team size |14|
| QP01 | Primary succeeded, one call | met/unknown/unmet/met/conflict/unmet; no extra training-proof duty or cross-order borrowing |6|

Three generation calls total; no fallback, regeneration or sampling retry. Eight exact
source excerpts totaling3,598 bytes were uploaded through the normal object-storage and
ingestion path. Its separate embedding ledger records eight document and eight query
calls, all responded. The three calls remain within the frozen six-call generation cap
and77 prior conservative reservations; the cap was not presented as an embedding cap.

Each row has one explicitly attributed Codex-assisted review. The review shortens the
long original answer and clarifies boundaries; original drafts, attempts, selected literal
evidence, all22/14/6 gaps and earlier business history remain unchanged. These known examples
pass the frozen assistant review; this is not independent domain/customer acceptance.

All three saved packets reread identically under v21 recovery and restored v24. Draft and
reviewed export modes each produce byte-identical XLSX/CSV across both switches. The only
changed original-workbook ZIP member is the selected worksheet; other worksheet content,
the unrelated formula and original ZIP members are retained. Review and both recovery
readbacks add no model calls, and their short-lived access tokens were revoked.

Local deliverables under `fallback-model-evidence/` in the central recovery group:

- `rc51-CQU06-reviewed-20261011.xlsx` and `.csv`
- `rc51-CQU05-reviewed-20261011.xlsx` and `.csv`
- `rc51-QP01-reviewed-20261011.xlsx` and `.csv`
- Separate `rc51-<case>-original-draft-20261011.xlsx/.csv` exports retain raw generation.
- `rc51-delivery-raw-semantic-review-20261011.json` records frozen-criterion judgments;
  `rc51-delivery-final-verification-20261011.json` binds packet IDs and file hashes.

Current recovery keeps rc51 readers and selects v21. rc50 cannot read the new extended
history and must not be restored by deleting or truncating records. The fixed delivery
continuation is complete; broad reliability, independent customer value and commercial
acceptance remain open. Prior failed and unknown observations are unchanged.

## Closeout and recovery evidence

After archiving the exact runtime records, removed only233 owned remote files and315
directories across the rc51 import/release/activate/rollback/reapply roots. Containerd
image references remained identical; all five workloads and public readiness passed
after cleanup. Removed the last verified local335,175,680-byte OCI transport archive
and its now-empty task temporary directory. The earlier transport-fragment cleanup
receipt remains separate. No historical backup or unrelated workspace file was deleted.

The central `manifest.json` retains original Git recovery and a verified documentation
checkpoint at the deployed commit, all strict/qualified release receipts, model originals,
review/export hashes and cleanup inventories. The233-file runtime archive excludes only
the verified disposable OCI transport and task-owned kubectl discovery cache, whose hashes
are recorded. Recovery images and release artifacts remain available. Scoped documentation
diff checks pass; the2993 pre-existing unrelated status entries remain byte-for-byte
unchanged. A whole-worktree whitespace check still reports pre-existing unrelated CRLF
content; none was modified for this release.
