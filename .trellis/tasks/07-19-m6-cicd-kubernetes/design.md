# M6 CI CD Kubernetes And Rollback: Design

## Delivery Shape

One source tree produces separate API, Worker/probe, Celery consumer and Web images.
The MCP server remains a private stdio child of the Worker; it is not exposed as a
public network endpoint. Kubernetes uses immutable image references and an additive
expand/migrate/contract database lifecycle.

## Rollout Order

1. Build, scan, generate SBOM and record digest.
2. Apply compatible ConfigMap/Secret references and migration Job.
3. Wait for migration Job success and required checkpointer setup.
4. Roll out API/Worker/consumer/Web with startup, readiness and liveness checks.
5. Run staging smoke and publish a release record.
6. Promote the exact digest or run a documented rollback. Never rebuild during promote.

## Security Boundaries

Containers run non-root with read-only root filesystems where compatible. ServiceAccounts
have no unnecessary API permissions. NetworkPolicy allows only required database,
Redis, object-store, API and DNS flows. Secrets are referenced, not committed.

## Evidence And Gates

Static manifest tests and local `kustomize build` are `passed` implementation evidence.
Registry push, image signature verification, cluster apply, TLS/ingress, backup/restore,
and staging smoke are separate manual gates. An unavailable cluster is not a successful
deployment result.
