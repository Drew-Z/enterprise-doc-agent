# M6 CI CD Kubernetes And Rollback: TDD Implementation Plan

## Slice 0: Contracts And Safe Runbooks

- [x] Add M6 Trellis contracts and external-gate rules.
- [x] Add Docker/Kubernetes/workflow contract tests and safe backup/rollback CLI help.

## Slice 1: Images

- [x] Add API, Worker/consumer and Web multi-stage Dockerfiles, nginx config and
  `.dockerignore`; pin runtime image digests where available.
- [x] Ensure non-root runtime and no source secrets in image layers; actual registry build remains
  an external execution gate.

## Slice 2: Kubernetes Base And Overlays

- [x] Add namespace/config/secret references, Deployments, Services, migration Job,
  probes, resources, PDB, RBAC and NetworkPolicy.
- [x] Add staging/prod overlays with image digest placeholders and explicit replicas.

## Slice 3: CI/CD And Supply Chain

- [x] Add image build/push, scan, SBOM, provenance, staging deploy, promotion and
  rollback workflows with manual environments.
- [x] Keep remote credentials and cluster operations as external gates.

## Slice 4: Backup/Restore And Release Records

- [x] Add non-destructive backup, restore-confirmation and kubectl rollback scripts.
- [x] Emit sanitized release/rollback records with digest, migration revision, RPO/RTO
  and operator fields.

## Slice 5: Evidence And Documentation

- [x] Render manifests, run static contracts, run available local image/compose checks,
  and record blocked external gates honestly.
- [x] Update README/spec and interview Q&A with real deployment boundaries.

## Completion Rules

No M6 task is archived from static files alone when the parent requires staging,
immutable image or rollback evidence. Those remain open manual gates.
