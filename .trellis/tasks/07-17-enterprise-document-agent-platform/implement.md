# Enterprise Document Agent Platform: Parent Implementation Plan

## Execution Order

1. Complete and archive `m0-project-foundation`.
2. Plan M1 and M2 from the real M0 repository contracts.
3. Implement M1 and M2; run their joint upload-complete→job-created integration gate.
4. Implement M3 on top of durable document versions and jobs.
5. Implement M4 on top of authorized retrieval and durable execution.
6. Implement M5 after the primary workflow is complete enough to measure.
7. Implement M6 only after tests, eval, probes, shutdown, and migration commands are real.
8. Implement M7 when provider baselines and hardware constraints are known.
9. Return to the parent task for full integration, staging, release, rollback, and documentation review.

## Continuous Queue — 2026-09-09

Current assessment: [continuous delivery state](research/20260909-continuous-delivery.md).

| Order | Child / owner | Prerequisite | Acceptance / transition |
|---|---|---|---|
| 1 | `09-09-staging-smoke-failure-evidence` / M6-R4 | Existing failed-run archive and smoke contract | Local validation passed: 120 focused/related, 1077 non-integration tests, Ruff and mypy; task in review pending commit |
| 2 | `09-09-consumer-attempt-attribution` / M5 | First child's local checks passed; consumer/attempt/log boundaries inspected | Local validation passed: 51 focused, 22 local integration, 1086 non-integration tests, Ruff and mypy; task in review pending commit |
| 3 | `09-09-delivery-staging-acceptance-plan` / M6 | Both local fixes validated; historical archive remains separate | [Four proposed work commits and future staging acceptance gates](../09-09-delivery-staging-acceptance-plan/delivery-plan.md) validated against all 128 dirty paths; local plan in review pending commit confirmation |

- [x] Reuse the existing parent and canonical `enterprise-doc-agent` checkout.
- [x] Review parent architecture, release limits, current worktree and failed-run evidence.
- [x] Create the first bounded child under M6 with requirements, design and validation plan.
- [x] Complete and validate child 1 locally, record source identity and [validation result](../09-09-staging-smoke-failure-evidence/validation.json).
- [x] Switch to planning child 2 from the current implementation and child 1 result.
- [x] Complete child 2 locally and record [validation](../09-09-consumer-attempt-attribution/validation.json).
- [x] Create and complete the next local delivery/acceptance-plan child.
- [ ] Obtain the separately required commit confirmation before committing or archiving local work.

The previous 86-file failure archive remains a separate pending delivery unit. No push, deployment, rollback, or real RAG evaluation follows automatically from a local child completing. M5/M7 external evidence and M6 independent recovery gates remain open.

## Parent Review Gates

Every gate consumes reviewed child evidence manifests. A checklist item is not complete when its command is still a planning placeholder, its environment is unknown, or its only evidence is a chat statement. External constraints use linked manual-gate records and remain blocking until satisfied.

### Gate A: Foundation

- M0 is archived.
- Real backend/frontend specs exist.
- Local services start with documented commands.

### Gate B: Durable data path

- M1 and M2 are archived.
- Multipart completion atomically creates a durable ingestion job.
- Duplicate completion and duplicate delivery tests pass.

### Gate C: Grounded AI path

- M3 and M4 are archived.
- Authorized upload→RAG→Agent→approval→artifact E2E passes.
- Citation, ACL, prompt-injection, and approval gates pass.

### Gate D: Production evidence

- M5 and M6 are archived.
- Load, fault, CI/CD, staging, deployment, and rollback evidence exists.
- Production-readiness evidence covers secrets, TLS/network boundaries, least-privilege identities, migration compatibility, database backup/restore, immutable image/SBOM/scans, audit access, monitoring/alerting, incident response, and rollback runbooks.

### Gate E: Model evidence

- M7 is archived, or an explicit approved scope decision records why this optional milestone is excluded from the release; unavailable hardware alone is a blocking gate, not completion evidence.
- Provider comparison distinguishes measured facts from targets.

## Final Validation

Commands will be finalized as child tasks establish the repository, but the parent must eventually run:

```text
backend lint + typecheck + unit
frontend lint + typecheck + unit
service integration
graph/tool/stream contracts
RAG/Agent/safety eval
Playwright E2E
Docker image build and scan
Kind/Kubernetes deployment smoke
k6 capacity suite
rollback drill verification
```

The final parent evidence index must point to the exact executable command or manual procedure, environment, commit/image version, result, and artifacts for every line above. `blocked_external` is reportable but does not satisfy a required parent gate.

## Parent Rollback Points

- Each child uses independent commits and must leave `main` runnable.
- Cross-child contract changes require versioned migrations and compatibility tests.
- A child is not archived if its only successful state depends on uncommitted local files or manual database edits.
- External manual gates remain visible in task artifacts and cannot be converted into completed checkboxes without evidence.
