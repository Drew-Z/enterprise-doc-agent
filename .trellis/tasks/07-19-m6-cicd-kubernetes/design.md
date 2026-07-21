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
4. Apply compatible prerequisites and run the migration Job.
5. Wait for migration Job success and required checkpointer setup.
6. Roll out API/Worker/consumer/Web with startup, readiness and liveness checks.
7. Run staging smoke and publish a release record.
8. Promote the exact digest or run a documented rollback. Never rebuild during promote.

## Security Boundaries

Containers run non-root with read-only root filesystems where compatible. ServiceAccounts
have no unnecessary API permissions. NetworkPolicy allows only required database,
Redis, object-store, API and DNS flows. Secrets are referenced, not committed.
External PostgreSQL egress is limited to the four database-using Pod identities and one
reviewed public `/32` or `/128`; the staging smoke has a dedicated label and API ingress
rule rather than receiving a general namespace exception.

## Evidence And Gates

Static manifest tests and local `kustomize build` are `passed` implementation evidence.
Registry push, image signature verification, cluster apply, TLS/ingress, backup/restore,
and staging smoke are separate manual gates. An unavailable cluster is not a successful
deployment result.

## Tiny Single-Node Staging

The `tiny-single-node` overlay inherits the ordinary staging contract but targets one
2-vCPU/2-GiB K3s node. It uses Traefik, one replica per process, an ephemeral bounded
Redis Deployment, and no PDB. PostgreSQL/pgvector, S3-compatible object storage, model
providers, and retained telemetry remain external. Redis is a delivery layer only;
PostgreSQL remains the source of truth and Outbox replay recovers lost Redis state.

The summed application-container memory limits stay at or below 1 GiB so K3s, the host,
and cloud management agents retain headroom. The 928 MiB ceiling includes simultaneous
existing workloads and migration, and application Deployments use `maxSurge: 0`. This is
a constrained staging and drill profile, not a production or high-availability topology.
A repository variable selects only reviewed profile names; the workflow checks Namespace
ownership before apply and writes the profile into the sanitized evidence manifest and
release record without exceeding GitHub's ten-input workflow dispatch limit.
