# Implementation Plan

1. [x] Add failing contract tests for final-digest supply-chain binding and Web
   object-origin build arguments.
2. [x] Refactor the container workflow so tag builds publish once, scan the published
   digest, verify signature/SBOM/provenance, and upload evidence on failure.
3. [x] Add Web build-arg plumbing and staging configuration contracts without
   committing secrets.
4. [x] Add or strengthen staging manifest/workflow evidence checks and render tests.
5. [x] Add offline recovery/capacity evidence validation where it can be tested
   deterministically; preserve `blocked_external` for cloud-only measurements.
6. [x] Run focused tests, full `pnpm quality`, Docker builds and Kustomize renders.
7. [x] Inspect GitHub access and repository target. No repository remote or target was
   available, so Actions/GHCR execution remains explicitly `blocked_external`.
8. [x] Record external blockers and evidence hashes; update the CI/CD spec with the
   reusable digest, staging and evidence conventions introduced by this task.
