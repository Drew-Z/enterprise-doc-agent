# Server Reset Audit

This began as a sanitized, read-only audit of the 2C/2G Baidu host before it
was considered for the tiny K3s staging node. It is intentionally not a backup
of credentials. The verified follow-up state is recorded separately in
`evidence/m6/20260722T0440-k3s-host-observation.json`.

## Observed workload inventory

| Area | Current evidence | Reset implication |
| --- | --- | --- |
| Fantasy Pet API | `fantasy-pet-app-api.service`, port `8765`, `/root/fantasy-pet-rule` | Stop only after source/config backup |
| Fantasy Pet worker | `fantasy-pet-worker-daemon.service` | Stop only after run artifacts are archived |
| Prometheus | Docker container `desktop-pet-prometheus`, loopback `9090`, about `2.8 MiB` TSDB | Preserve config/rules; history is optional |
| Gamer admin review | `/root/gamer`, Node `apps/admin-review/server.js`, port `14200` | Preserve source and database variable names; `.env.local` is excluded |
| Browser bridge | `/root/GenericAgent`, loopback ports `18765/18766` | Preserve the working-tree diff; do not copy provider keys |
| Codex app server | Unix socket under `/root` | Not part of the staging workload; terminate with the old host |
| Remote desktop | `xrdp` on `3389` | Remove on the dedicated staging image |
| Cloud/vendor agents | `bcm-agent`, `bsm-agent`, `heyeAgent`, `has-agent` | Replaced by the clean image/provider agent policy |

## Backup policy

The pre-reset archive contains source, unit definitions, Prometheus config/rules,
and SHA-256 manifests. It excludes `.env*`, private operation files, provider
key files, `mykey.py*`, virtual environments, caches, node modules, and raw
tokens. Keep the archive outside Git and copy it to a second machine before
resetting the host.

The sanitized off-host archive was captured as
`tmp/server-backups/enterprise-doc-server-backup-final-20260722T034951Z.tar.gz`.
Its remote and local SHA-256 is
`cb492c6c87e0fd6317eedd56ce7d4594669e1b759d8cddb6b6387135a65bca2a`.
The final archive contains 1,407 entries, is 48,565,353 bytes, and its filename
audit found no excluded credential, key, token, cache, runtime or nested-archive
paths. The archive is intentionally under the gitignored `tmp/` tree.

## Follow-up state

The sanitized off-host backup was verified, the Fantasy Pet API and worker,
Gamer review service, Prometheus, and XRDP were stopped, and K3s
`v1.36.2+k3s1` was installed. The node and its packaged CoreDNS, metrics-server,
local-path-provisioner, and Traefik Pods are Ready.

This does not make the host staging-ready. Before application rollout, the
host still needs a private control-plane boundary: UFW was inactive and K3s was
listening on `6443/tcp`, `10250/tcp`, and `8472/udp`. Apply provider security
group and host firewall rules from an out-of-band console while preserving the
operator SSH allowlist. A repository-scoped non-root runner, external
PostgreSQL, R2 credentials, Tunnel/TLS, and authenticated smoke are also still
open gates.

## Reset gate

The host is **not reset-ready** until all of these are true:

- the archive has a verified SHA-256 manifest and a second copy;
- the `gamer` service owner confirms port `14200` can be retired;
- the desktop-pet API/worker owner confirms outstanding jobs are not needed;
- Prometheus rules and the desired historical retention decision are recorded;
- external PostgreSQL, object storage, model credentials, TLS and GHCR pull
  credentials are provisioned independently;
- SSH access to the freshly installed node has been tested from the operator
  workstation.

The source trees remain on disk and can be restored from the verified archive;
no destructive source deletion is implied by the service retirement. Do not
perform additional destructive cleanup as a substitute for the remaining
network, identity, dependency, and smoke gates.
