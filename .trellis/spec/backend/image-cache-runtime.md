# Containerd Import Aliases Before Release

## Scope / Trigger

Use for OCI imports into the current k3s node before a release or rollback. On
2026-09-28, all eight approved image references and CRI ImageStatus were present,
but the new API could not start because CRI also retained an absent imported alias.

## Signatures

Run these argument lists on the Linux host via the existing SSH transport:

```text
k3s ctr -n k8s.io images ls
k3s crictl inspecti <approved-image@sha256:digest>
k3s ctr -n k8s.io images tag <approved-image@sha256:digest> <verified-missing-alias>
```

Pass separate subprocess arguments; do not construct shell code from registry
responses. Image tagging here creates a local reference, not a remote registry tag.

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

## Validation & Error Matrix

| Observation | Required outcome |
| --- | --- |
| Approved source and all relevant aliases agree | Cache-reference check passes; container startup is still a separate check |
| CRI alias absent from containerd store | Cache check fails; prepare exact same-digest alias repair before downtime |
| Source/alias digest differs or identity is unclear | Refuse tagging; retain evidence |
| Tag command response unknown | Read back both references before any retry |
| Active switch reaches original deadline | Existing executor owns recovery; no new arm or budget reset |

## Good/Base/Bad Cases

Good: verify a missing `docker.io/library/import-2026-09-28@sha256:...` binding,
create that alias from the approved source and read both TARGET digests back.
Base: inspect an already consistent cache without mutation.
Bad: count eight image names and claim all containers are ready to start.

## Tests Required

Future reusable prewarm validation must exercise missing/stale/mismatched aliases,
unknown tag results and unchanged deadlines through the subprocess boundary.
Actual release evidence must additionally retain Pod startup/readiness and image
identity. This document records a real incident/repair; it does not claim the
current runtime preflight automatically enforces alias completeness.

## Wrong vs Correct

Wrong: a successful `crictl inspecti` or image-list count proves the import is usable.
Correct: reconcile approved digest, CRI aliases and containerd references, then
separately verify actual container startup without altering the approved plan.

## Proven Examples

- `docs/ops/release-switch-runbook.md`
- `.trellis/tasks/09-24-commercial-operations-acceptance/release-switch-execution-validation.json`
- `scripts/release_switch.py`
- `tests/deployment/test_release_switch.py` (existing executor deadline/fence behavior;
  not automatic import-alias validation)
