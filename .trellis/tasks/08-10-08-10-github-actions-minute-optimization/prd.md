# GitHub Actions minute optimization

## Goal

Reduce billed GitHub-hosted runner minutes for the private Enterprise Document Agent repository while retaining fast automated code feedback and explicit access to the full integration/e2e evidence suite.

## Background

- The personal account has a zero-dollar Actions budget with stop-usage enabled.
- From 2026-08-01 through 2026-08-10, the repository ran about 45 Quality workflows, 22 Staging deploy workflows, 16 container matrix jobs, and four image-relay workflows.
- `quality.yml` currently starts five independent jobs for every pull request and every push to `main`: backend, frontend, M1 integration, M4 integration, and Web E2E. Billing accumulates runner time per parallel job.
- Staging deployment and image relay are already explicit manual workflows and remain unchanged.
- The container workflow starts four image matrix jobs for every PR regardless of changed paths.
- The private repository cannot use branch protection on the current GitHub plan.
- Active M5/M6/M7 and RAG-quality work exists in the primary worktree. This task is isolated in its own branch and worktree from `origin/main`.

## Requirements

- Keep automated backend and frontend quality on pull requests and `main` pushes that change executable, dependency, test, workflow, or configuration paths.
- Skip automated Quality for documentation/Trellis-record-only changes.
- Move M1 integration, M4 integration/evaluation, and Web E2E jobs into a separate manual `workflow_dispatch` workflow without weakening their commands, cleanup, artifact, or no-allow-failure contracts.
- Restrict pull-request container matrix runs to container/application/dependency/supply-chain paths; tagged and manual container builds remain available.
- Preserve staging deployment, image relay, rollback, provider, Kubernetes, release evidence, and secret boundaries.
- Update foundation CI contracts and backend specifications to describe fast automatic gates versus full manual evidence gates.
- Do not trigger staging deployment, container publication, live model evaluation, or production infrastructure during validation.

## Acceptance Criteria

- [x] Automatic `quality.yml` contains only backend and frontend jobs and ignores documentation/Trellis-only changes for PR and `main` push events.
- [x] A separate manual workflow contains M1 integration, M4 integration/evaluation, and Web E2E jobs with their existing commands, cleanup, timeouts, and evidence artifacts intact.
- [x] `container.yml` keeps tag/manual behavior and adds a reviewed PR path filter covering all inputs that can affect the four images.
- [x] Foundation tests parse both Quality workflows and enforce trigger separation, commands, evidence, cleanup, and the absence of allow-failure/retry paths.
- [x] Backend CI/CD, multipart-operations, and quality specifications document the new execution boundary.
- [x] Focused CI contract tests, repository quality checks, YAML parsing, and `git diff --check` pass without live provider/infrastructure calls.
- [ ] The branch is pushed and reviewed through a PR without touching the primary dirty worktree or active M5/M6/M7/RAG tasks.

## Out Of Scope

- Modifying staging, rollback, image-relay, Kubernetes, model-provider, or runtime application behavior.
- Self-hosted runners, paid GitHub plans, or making the repository public.
- Running manual heavy CI, publishing images, or performing a staging deployment during this task.
- Rewriting the current test suites or weakening assertions to reduce runtime.
