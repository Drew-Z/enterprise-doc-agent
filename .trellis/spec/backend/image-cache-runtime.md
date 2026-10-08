# Image Cache Capacity and Import Aliases Before Release

## Scope / Trigger

Use for OCI imports into the current k3s node before a release or rollback. On
2026-09-28, all eight approved image references and CRI ImageStatus were present,
but the new API could not start because CRI also retained an absent imported alias.
The rc.41 prewarm later exhausted the root disk and evicted running workloads.
All candidate archives must therefore be evaluated as one batch before any cache write.

## Signatures

Run these argument lists on the Linux host via the existing SSH transport:

```text
k3s ctr -n k8s.io images ls
k3s crictl inspecti <approved-image@sha256:digest>
k3s ctr -n k8s.io images tag <approved-image@sha256:digest> <verified-missing-alias>
```

Pass separate subprocess arguments; do not construct shell code from registry
responses. Image tagging here creates a local reference, not a remote registry tag.

The versioned receiver requires its sibling `scripts/image_cache_safety.py`.
`execute_import_batch(plans, run=..., probe=..., clock=..., timeout=600)` is the
public write boundary; `execute_import_plan` delegates to the same guard.
The CLI accepts `--batch-plan <file>` and defaults to an offline plan. The JSON
object has `schema_version: 1` and `archives`, with one to sixteen entries containing
`archive`, `expected_sha256`, `base_name` and optional `image_reference`. Relative
archive paths resolve against the plan directory. `--confirm` enables the guarded
import. Live observation supports the reviewed Linux single-node amd64 k3s target;
missing facts or unsupported targets are rejected, never treated as zero usage.

## Contracts

- Bind component/source/index digest to the signed release manifest and retain
  original/current/candidate identities separately.
- Compare each relevant CRI `status.repoDigests` entry with the actual containerd
  image-store `REF` and `DIGEST` columns. ImageStatus can return stale aliases.
- The approved source and a repaired alias must have the same exact TARGET digest.
  Inspect OCI index/platform membership separately when runtime digest types differ.
- A missing alias may be created only after its binding is proven. Never use
  `--force`, replace image content, delete unknown references or restart k3s/CRI to
  hide the failure. Preserve baseline/repair/readback and the failed Pod state.
- Check candidate and rollback references before stopping service. A repair during
  an active switch does not re-arm, restart the switch or extend its deadline.
- Do not remove required aliases as transport cleanup. Keep them while the runtime
  or a retained rollback depends on them.
- Verify the archive SHA again at execution, all content digests/sizes, native
  gzip/plain-tar layers and expanded `diff_id` before normalization. Partial relay
  archives, sparse layers and unsupported encodings are rejected. Count expanded
  OCI envelope size for normalization, not only compressed transport bytes.
- BuildKit can encode attestations as `application/vnd.docker.attestation.manifest.v1+json`
  with `application/vnd.oci.empty.v1+json` config containing exactly `{}`. Require
  matching inline config when present, only in-toto payload layers, and a same-archive
  linux/amd64 runtime subject with matching descriptor digest/type/size. Count all
  artifact content, inodes and normalized copies, but no runtime snapshot. Unknown
  empty configs, runtime layers disguised as proof, and unbound subjects reject
  before temporary files or containerd commands. Legacy unknown/unknown configs
  retain their existing compatibility path; missing platform fields alone never
  establish that arbitrary content is a non-runtime artifact.
- Sum the full batch's content, snapshots and temporary copies. Shared layers may
  be counted repeatedly. Cache allocation includes 25% headroom, 64 MiB metadata
  and 4096 metadata inodes in addition to content-store and snapshot file counts;
  preserve at least 5% inodes plus any stricter policy.
  Group allocations by filesystem device and keep separate node, cache and temporary
  filesystem checks. Preserve `max(evictionHard, evictionSoft) + minimumReclaim`
  for each applicable signal; never lower the effective kubelet policy.
- Before temporary-file creation, before every import and after the batch, inspect
  all five original Deployments, including Redis and every init/ordinary container.
  Require immutable canonical digests, `ctr images check --quiet` readiness (complete
  content and unpacked snapshots), CRI aliases and exact containerd TARGET digests.
  Samples are fresh for 15 seconds measured from observation start. Freeze Namespace,
  Node, boot, full workload specs, required refs, policy and filesystem identities.
- Normalization is followed by source SHA and copied-content revalidation before
  the first import. A single monotonic deadline covers the batch; command timeouts
  cannot restart it. A timed-out import has an unknown outcome and is not retried.
  Temporary normalized files are removed on exit; cache failures do not trigger
  deletion, GC, service changes or an automatic release.

## Validation & Error Matrix

| Observation | Required outcome |
| --- | --- |
| Approved source and all relevant aliases agree | Cache-reference check passes; container startup is still a separate check |
| CRI alias absent from containerd store | Cache check fails; prepare exact same-digest alias repair before downtime |
| Source/alias digest differs or identity is unclear | Refuse tagging; retain evidence |
| Tag command response unknown | Read back both references before any retry |
| Active switch reaches original deadline | Existing executor owns recovery; no new arm or budget reset |
| One archive fits but aggregate batch does not | Reject before normalization or containerd writes |
| Insufficient bytes/inodes, stale sample, policy/spec/boot/device drift | Reject the next write; retain failure |
| Redis/init image missing or not unpacked, stale CRI alias | Reject prewarm even when four application names exist |
| Original cache disappears after import | Batch fails; do not publish a passed receipt |
| Exact BuildKit empty-config artifact bound to a supported runtime | Count proof content/inodes and temporary bytes; no artifact snapshot |
| Unknown empty config, runtime payload type or mismatched artifact subject | Reject before temporary archive or cache commands |

## Good/Base/Bad Cases

Good: verify a missing `docker.io/library/import-2026-09-28@sha256:...` binding,
create that alias from the approved source and read both TARGET digests back.
Base: inspect an already consistent cache without mutation.
Bad: count eight image names and claim all containers are ready to start.
Good: pass all four candidate archives in one reviewed batch and retain all three
guard receipts. Base: produce an offline plan, which does not prove live capacity.
Bad: import archives independently to hide aggregate peak usage or reuse the old
partial-blob transport outside this complete-archive guard.
Good: a verified empty-config BuildKit proof accompanies its amd64 runtime.
Base: the earlier unknown/unknown proof format remains supported.
Bad: skip every config lacking platform fields or exclude proof files from capacity.

## Tests Required

Public receiver tests exercise aggregate refusal with zero commands/files, real
gzip layer/diff-id validation, outer envelope expansion, original Redis/init cache,
stale/mismatched aliases, policy changes, separate full node disks, post-import
cache loss, unknown import results and shared deadlines through system boundaries.
Include current OCI empty-config attestations, legacy proofs and malformed artifact
type/config/payload/subject cases. Verify that proof bytes/inodes remain budgeted
and that unsupported artifacts create no temporary archive or import command.
Actual release evidence must additionally retain Pod startup/readiness and image
identity. Local tests and a live refusal do not establish a successful new release.

## Wrong vs Correct

Wrong: a successful `crictl inspecti` or image-list count proves the import is usable.
Correct: reconcile approved digest, CRI aliases and containerd references, then
separately verify actual container startup without altering the approved plan.
Wrong: an OCI config without an OS must be safe to ignore.
Correct: recognize the exact artifact contract and bound subject, retaining all
content and temporary-allocation accounting before excluding only its snapshot.

## Proven Examples

- `docs/ops/release-switch-runbook.md`
- `.trellis/tasks/09-24-commercial-operations-acceptance/release-switch-execution-validation.json`
- `scripts/release_switch.py`
- `scripts/import_staging_oci_archive.py`
- `scripts/image_cache_safety.py`
- `tests/deployment/test_image_cache_safety.py`
- `tests/deployment/test_release_switch.py` (existing executor deadline/fence behavior;
  not automatic import-alias validation)
