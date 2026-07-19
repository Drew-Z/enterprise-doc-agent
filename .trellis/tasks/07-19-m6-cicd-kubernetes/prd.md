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
  graceful termination, PDB, ServiceAccount/RBAC and NetworkPolicy.
- **M6-R4**: Staging overlays can apply with environment-specific images and replicas;
  migrations run before rollout and the upload -> ingestion -> Agent smoke is explicit.
- **M6-R5**: Release promotion and rollback workflows record image digest, migration
  revision, operator, timestamps and result. Rollback does not assume destructive schema
  changes are reversible.
- **M6-R6**: Backup/restore procedures are non-destructive by default, require explicit
  confirmation, redact credentials, and record RPO/RTO observations.
- **M6-R7**: Missing cloud registry, cluster, TLS, secret manager or production backup
  access is represented as `blocked_external`, never as a passed deployment result.

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

## Out Of Scope

Cloud account provisioning, managed database durability, certificate issuance, secret
manager provisioning, and a claim of production availability.
