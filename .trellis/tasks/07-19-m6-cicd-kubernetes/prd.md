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
- **M6-R11**: A reviewed 4-vCPU/8-GiB single-node staging profile preserves the same
  external-state and administrator-owned prerequisite boundaries, runs two API and Web
  replicas for concurrency drills, keeps Worker/consumer/Redis singletons, removes PDBs,
  uses zero-surge rollout, and leaves at least 2 GiB of node memory outside declared
  application plus migration limits for K3s, the runner, tunnel and host daemons.
- **M6-R12**: Ubuntu 24.04 host preparation is repository-owned and reproducible. It
  fail-closes without an explicit operator SSH CIDR, hardens SSH only after key access is
  verified, enables a host firewall without exposing K3s control-plane ports, disables
  swap, configures required kernel modules/sysctls and bounded logs, and separates the
  pinned K3s installation from the baseline step so every mutation is reviewable.
- **M6-R13**: K3s, Kustomize and the deployment-runner toolchain use reviewed exact
  versions. The host keeps the GitHub runner repository-scoped and non-root, keeps the
  Kubernetes API on loopback for deployment jobs, and does not install application
  secrets, tunnel credentials or runner registration tokens from repository files.
- **M6-R14**: Restricted-network OCI relay imports are repository-owned, checksum-verified,
  dry-run-first, and use a fully qualified containerd base name. Every imported image index
  and manifest digest must have a resolvable canonical alias before staging deploy can start.

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
- [x] The 4C8G profile renders with the reviewed replica/resource shape, ephemeral Redis,
  no PDB, zero-surge updates, external durable dependencies and a peak application plus
  migration memory ceiling of 6 GiB.
- [x] Host baseline assets have contract tests for Ubuntu 24.04, explicit SSH allowlist,
  SSH hardening, UFW/CNI safety, swap removal, kernel/sysctl readiness, bounded journald,
  and a non-mutating check mode.
- [x] The K3s installer pins `v1.36.2+k3s1`, retains packaged Traefik, reserves node
  resources, never exposes the API through Cloudflare Tunnel, and verifies Ready system
  Pods before bootstrap manifests are applied.
- [x] The OCI relay receipt names the versioned receiver contract, and the receiver verifies
  archive SHA-256 plus canonical `docker.io/library/...@sha256:...` aliases for every image
  index and manifest descriptor instead of requiring manual containerd tags.

## Out Of Scope

Cloud account provisioning, managed database durability, certificate issuance, secret
manager provisioning, and a claim of production availability.
