# M6 CI CD Kubernetes And Rollback: Design

## Delivery Shape

One source tree produces separate API, Worker/probe, Celery consumer and Web images.
The MCP server remains a private stdio child of the Worker; it is not exposed as a
public network endpoint. Kubernetes uses immutable image references and an additive
expand/migrate/contract database lifecycle.

## Rollout Order

1. Build, scan, generate SBOM and record digest.
2. Reject an existing unannotated or mismatched deployment-profile Namespace.
3. Bind exact digests in the staging parent overlay, then render the selected profile.
4. Verify the administrator-applied prerequisite fingerprint, then run the migration Job.
5. Wait for migration Job success and required checkpointer setup.
6. Roll out API/Worker/consumer/Web with startup, readiness and liveness checks.
7. Run staging smoke and publish a release record.
8. Promote the exact digest or run a documented rollback. Never rebuild during promote.

The migration Job has a fixed reviewed name and an immutable Pod template. On repeated
releases, the workflow verifies prerequisites and workload updates first, deletes the
completed old migration Job, then performs a server-side create dry-run before applying
the new Job. Dry-running an update against the old Job would fail on immutable template
fields even when the new migration is valid.

## Security Boundaries

Containers run non-root with read-only root filesystems where compatible. ServiceAccounts
have no unnecessary API permissions. NetworkPolicy allows only required database,
Redis, object-store, API and DNS flows. Secrets are referenced, not committed.
External PostgreSQL egress is limited to the four database-using Pod identities and one
reviewed public `/32` or `/128`; the staging smoke has a dedicated label and API ingress
rule rather than receiving a general namespace exception.

Staging prerequisites are an administrator boundary: Namespace metadata, ConfigMap,
ServiceAccount, Service, Ingress, NetworkPolicy and PDB objects are readable but immutable
to the release deployer. Administrator-approved endpoints, model routing and current plus
one rollback image per service are recorded as Namespace annotations. Deployment and Job
admission policies read those immutable annotations through `namespaceObject`; they do not
use `paramKind` because Kubernetes 1.36 can miss newly recreated parameter objects in its
admission informer cache before evaluating match conditions. Admission is defense in depth
for the scoped deployer, not isolation from cluster administrators or from credentials that
an approved workload is intentionally allowed to consume.

The staging workflow renders the expected prerequisite approval fingerprint, verifies the
live administrator-owned Namespace record, and compares the live prerequisite inventory
and normalized specs with the approved render, then applies only migration and workload
phases. Kubernetes Secret content is validated in a separate administrator-side preflight
and never read by the scoped deployer workflow. Job deletion is limited by resource name to
the migration and readiness Jobs. Kubeconfig is written only to a job-unique temporary path
with signal/error and final-step cleanup.

Rollback first submits a server-side dry-run undo for every target revision. Only when all
preflights pass does it submit all actual undo requests, followed by rollout health waits.
The Namespace allowlist retains the current and immediately previous immutable image for
each application service so reviewed rollback remains possible without allowing arbitrary
repositories or tags.

This ordering is an all-target preflight, not a Kubernetes transaction. A failure during
actual undo can still leave mixed revisions; the structured command record identifies the
last completed mutation for explicit operator reconciliation.

## Evidence And Gates

Static manifest tests and local `kustomize build` are `passed` implementation evidence.
Registry push, image signature verification, cluster apply, TLS/ingress, backup/restore,
and staging smoke are separate manual gates. An unavailable cluster is not a successful
deployment result.

## R2 Recovery Boundary

Cloudflare R2 does not provide the S3 bucket-versioning APIs required for a native
`VersionId` restore workflow. Recovery therefore uses an application-owned immutable
snapshot namespace. The snapshot command reads object references from the reviewed
database schema, verifies every source object by size and streamed SHA-256, conditionally
copies it below `enterprise-doc-recovery/snapshots/<drill-id>/`, and writes a digested
manifest only after every copy has been read back successfully.

Restore is dry-run by default and accepts only a manifest whose digest, endpoint host,
bucket allowlist and snapshot prefix match the operator's explicit expectations. A
confirmed restore is additionally restricted to an `enterprise_doc_restore_` database and
copies objects below `enterprise-doc-recovery/restores/<restore-id>/`; it never overwrites
application keys. The final check compares database references, manifest entries and the
listed restored object set in both directions. Credentials remain environment-only, while
manifests containing object keys are private artifacts written with owner-only permissions.

## Tiny Single-Node Staging

The `tiny-single-node` overlay inherits the ordinary staging contract but targets one
2-vCPU/2-GiB K3s node. It uses Traefik, one replica per process, an ephemeral bounded
Redis Deployment, and no PDB. PostgreSQL/pgvector, S3-compatible object storage, model
providers, and retained telemetry remain external. Redis is a delivery layer only;
PostgreSQL remains the source of truth and Outbox replay recovers lost Redis state.

The summed application-container memory limits stay at or below 1 GiB so K3s, the runner,
the tunnel, and required host daemons retain headroom. The 992 MiB ceiling includes
simultaneous existing workloads and migration, and application Deployments use
`maxSurge: 0`. This is
a constrained staging and drill profile, not a production or high-availability topology.
A repository variable selects only reviewed profile names; the workflow checks Namespace
ownership before apply and writes the profile into the sanitized evidence manifest and
release record without exceeding GitHub's ten-input workflow dispatch limit.

## Staging Model Contract

Staging fixes the provider to `openai_compatible`. The protected GitHub Environment
supplies the non-secret HTTPS `/v1` gateway URL and exact model identifier, while the
Kubernetes application Secret supplies only `MODEL__API_KEY`. These values are validated
during render and recorded in the administrator-owned prerequisite approval. A hash of the
resulting ConfigMap is placed on API, Worker, consumer and migration Pod templates so a
routing change triggers replacement. The sanitized release record retains only provider,
URL and model name for reproducibility.

## Measured Tiny-Staging Dependency Budgets

The real 2-vCPU/2-GiB staging node uses external PostgreSQL and R2, so the staging overlay
sets 15-second database/object-store connection budgets and a 60-second checkpointer
budget without changing production defaults. Worker readiness includes the checkpointer
budget, while the tiny probe timeout exceeds it. The migration Job runs Alembic, official
LangGraph setup, and a read-only schema check in that order before workloads roll out.

## Four-Core Single-Node Staging

`single-node-4c8g` is a separate reviewed profile rather than a renamed tiny profile. It
inherits the staging routing, immutable-image, secret, NetworkPolicy and administrator
prerequisite contracts. It uses two API replicas and two Web replicas to exercise request
distribution, while Worker, consumer and the ephemeral Redis delivery layer remain one
replica because the node is still a single failure domain. PDBs remain absent and all
application Deployments use `maxSurge: 0`; replica count does not imply host availability.

The final render may declare at most 6 GiB of memory limits across simultaneously running
application containers plus the migration Job. Requests must fit below 3 GiB and 3 CPU so
the scheduler retains room for K3s system Pods. This leaves at least 2 GiB of physical
memory outside the declared peak for the kernel, K3s, runner, tunnel, telemetry and image
pull/decompression bursts. PostgreSQL/pgvector, R2, chat/embedding providers and retained
telemetry stay external. The first release remains a platform/orchestration drill with a
real chat route and deterministic 8-dimensional embeddings until the separately reviewed
embedding schema/reindex milestone is complete.

## Reproducible Ubuntu Host Boundary

Host preparation is split into reviewable stages:

1. `bootstrap-host.sh --check` performs a read-only Ubuntu/version/capacity/access audit.
2. `bootstrap-host.sh --apply --operator-ssh-cidr <CIDR>` upgrades the OS, installs only
   baseline utilities, configures modules/sysctls/journald, disables swap, hardens SSH and
   enables UFW after the explicit operator allow rule exists. It preserves Tencent TAT as
   break-glass access and does not install application credentials.
3. `install-k3s.sh` installs exactly `v1.36.2+k3s1`, writes a root-only K3s config with
   kube/system reservations, retains packaged Traefik and verifies the node/system Pods.
4. `provision-runner-toolchain.sh` installs the exact kubectl/Kustomize/Python dependency
   contract under root-owned paths. Runner registration and Cloudflare Tunnel enrollment
   remain explicit token-bearing operator steps outside Git.

The host firewall permits SSH only from the supplied operator CIDR, permits local CNI
interfaces and Pod/Service CIDRs required by K3s, and denies public ingress to 80, 443,
6443, 10250 and 8472. ServiceLB is disabled because its hostPort path can bypass ordinary
UFW input filtering. Packaged Traefik remains enabled as a ClusterIP Service and binds
only `127.0.0.1:8080` and `127.0.0.1:8443`; the host cloudflared connector uses the TLS
loopback endpoint. SSH hardening is loaded through a drop-in and validated with `sshd -t`
before reload; the active key session and provider console are mandatory rollback paths.

## Restricted-Network OCI Relay Import

The GitHub-hosted relay exports every release image as a digest-preserving OCI archive and
records the expected archive checksum plus receiver base name. The staging node imports the
archive through `scripts/import_staging_oci_archive.py`, which is dry-run-only unless the
operator passes `--confirm`. The receiver streams the archive checksum, parses OCI metadata
without extracting files, validates each image index/manifest descriptor, and supplies a
fully qualified `docker.io/library/<relay-id>` base to `ctr images import --digests`.

After import, the receiver requires and inspects a canonical digest image record for every
validated index and manifest descriptor. Checking only the top-level application index is
insufficient because containerd and CRI may resolve nested runtime or attestation manifests
by their normalized name. A short `--base-name` can create records such as
`import-date@sha256:...` while CRI asks for `docker.io/library/import-date@sha256:...`; the
versioned receiver prevents that namespace mismatch before staging workloads are applied.
