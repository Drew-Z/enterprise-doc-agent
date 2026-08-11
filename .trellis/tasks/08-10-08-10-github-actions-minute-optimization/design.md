# GitHub Actions minute optimization design

## Automatic Quality Boundary

`quality.yml` remains triggered by pull requests and pushes to `main`, but uses `paths-ignore` for documentation and Trellis record paths. It contains only backend and frontend jobs. Existing workflow concurrency continues to cancel obsolete runs on the same ref.

This preserves rapid static/unit/build feedback while removing three expensive service/browser jobs from every code event.

## Manual Full-Evidence Boundary

The existing M1 integration, M4 integration/evaluation, and Web E2E job definitions move without semantic changes to `quality-heavy.yml`, triggered only by `workflow_dispatch`. The workflow keeps independent jobs so evidence and failure ownership remain clear. Cleanup and `always()` artifact behavior remain unchanged.

## Container Boundary

`container.yml` retains tag and manual triggers. Its PR trigger gains path filters for Dockerfiles, application/core sources, dependency locks/manifests, container workflow/test contracts, and release configuration. A path that can alter an image or supply-chain assertion must still trigger all four matrix jobs; unrelated docs/Trellis changes do not.

## Compatibility And Rollback

No application or deployment contract changes. Existing heavy job names and commands are preserved in the manual workflow for discoverability. Rollback restores the jobs to `quality.yml` and removes the PR container path filter.

## Validation

Foundation tests parse both workflows and assert trigger separation plus exact job contracts. Local validation runs focused CI contract tests and repository quality commands that do not open live provider or staging connections.
