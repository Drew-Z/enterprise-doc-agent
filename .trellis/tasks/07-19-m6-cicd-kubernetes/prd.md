# M6 CI CD Kubernetes And Rollback

## Goal

Turn the local modular monorepo into a reproducible delivery artifact. M6 owns
container build contracts, supply-chain metadata, Kubernetes staging manifests,
migrations, readiness and graceful shutdown, promotion, backup and rollback.

Static and local checks are valid implementation evidence. They do not claim a
cloud deployment or production release without an external gate record.

## Requirements

- **M6-R1**: API, Worker, consumer, MCP-capable Worker, and Web images build from
  pinned runtime bases and run as non-root users with bounded writable paths.
- **M6-R2**: CI runs lint, tests, image build, vulnerability scan, SBOM and provenance
  checks; promotion uses immutable image digests and never `latest`.
- **M6-R3**: Kubernetes base manifests define Deployments/Services, migration Job,
  ConfigMap/secret references, startup/liveness/readiness probes, resources,
  graceful termination, PDB, ServiceAccount and NetworkPolicy. Administrator bootstrap
  manifests define the scoped deployer RBAC and admission guardrails separately.
- **M6-R4**: Staging overlays can apply with environment-specific images and replicas;
  migrations run before rollout and the upload -> ingestion -> Agent smoke is explicit.
- **M6-R5**: Release promotion and rollback workflows record image digest, migration
  revision, operator, timestamps and result. Rollback does not assume destructive schema
  changes are reversible.
- **M6-R6**: Backup/restore procedures are non-destructive by default, require explicit
  confirmation, redact credentials, and record RPO/RTO observations.
- **M6-R7**: Missing cloud registry, cluster, TLS, secret manager or production backup
  access is represented as `blocked_external`, never as a passed deployment result.
- **M6-R8**: The reviewed 2-vCPU/2-GiB staging profile keeps durable business state
  external, provides a bounded Redis delivery layer, targets K3s Traefik, removes
  misleading single-node PDBs, keeps migration-overlap memory within 1 GiB, and records
  the selected profile in both Namespace ownership and hashed release evidence.
- **M6-R9**: Staging infrastructure prerequisites remain administrator-owned. The scoped
  deployer can mutate only reviewed Deployments and Jobs, cannot read Kubernetes Secrets,
  can delete only the migration and readiness Jobs, and is constrained by admission rules
  whose approved endpoints and immutable image set come from administrator-owned Namespace
  annotations rather than eventually consistent admission parameter caches. Deployment
  also verifies the live prerequisite inventory and normalized specs against that approval.
- **M6-R10**: Rollback preflights every requested Deployment revision through the API
  server before mutating any Deployment, then submits all rollback specs before waiting
  for health so an admission or revision error cannot create an avoidable mixed release.

## Acceptance Criteria

- [x] All service Dockerfiles build or fail with an explicit environment gate.
- [x] Static tests reject root containers, floating tags, plaintext production secrets,
  missing probes, missing resource limits and missing security contexts.
- [x] Kustomize base and staging overlays render successfully.
- [x] Migration Job and rollout ordering are documented and contract tested.
- [x] CI workflow contains build, scan, SBOM, digest publication, staging smoke and
  manual promotion/rollback boundaries.
- [x] Backup, restore and rollback scripts have safe `--help`/dry-run paths and require
  explicit confirmation for destructive actions.
- [x] Real registry/cluster/staging/production evidence is either present or linked to
  an open external manual gate.
- [x] The tiny single-node overlay renders to one replica per process, no PDBs, no
  in-cluster PostgreSQL/MinIO, zero-surge application rollouts, and at most 1 GiB of
  memory limits when existing workloads overlap the migration Job.
- [x] Staging smoke identity, profile ownership, exact digest binding, and public
  single-host PostgreSQL egress are final-render and workflow contract tested.
- [x] Admin-owned staging prerequisites, deployer RBAC denials, and parameter-free
  admission allow/deny behavior are contract tested and verified against a real API server.
- [x] Rollback performs all server-side dry-run preflights before any rollout undo and
  keeps structured partial-failure evidence for rollout health failures.

## Out Of Scope

Cloud account provisioning, managed database durability, certificate issuance, secret
manager provisioning, and a claim of production availability.
