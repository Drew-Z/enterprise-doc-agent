# Enterprise Document Agent Platform

## Goal

Build a complete, evidence-backed enterprise document Agent platform that closes the candidate's current gaps in Python backend engineering, large-file transfer, durable async work, RAG, Agent orchestration, MCP, observability, CI/CD, Kubernetes, and production operations.

The finished system must provide one real end-to-end workflow:

```text
resumable document upload
→ durable ingestion job
→ parse/chunk/embed/index
→ grounded Agent task
→ citation and permission gates
→ optional human approval
→ auditable result artifact
```

The platform is an engineering project first. A feature is not considered complete until its behavior is tested and its runtime evidence is recorded.

## Background

- Existing projects already demonstrate document RAG, file-based Worker orchestration, Redis queues, fixed LangGraph workflows, tool guardrails, and human review.
- Existing implementations also expose the gaps this project must correct: file-backed state, non-atomic queue writes, incomplete tenant ACL, no durable graph checkpointing, no full MCP authorization, and limited deployment evidence.
- Trellis manages planning, implementation, verification, specification capture, and task archives. It does not replace the runtime components.
- The implementation is organized as one parent task and milestone child tasks M0 through M7.

## Requirements

### Product requirements

- **PR-1**: Users can upload TXT, PDF, and DOCX documents through resumable S3-compatible Multipart upload.
- **PR-2**: APIs that create uploads, jobs, and Agent runs return quickly and never execute model or parsing work in the request process.
- **PR-3**: Uploaded documents can be parsed, chunked, embedded, and indexed through durable asynchronous jobs.
- **PR-4**: Users can run question answering, summarization, and structured extraction tasks against authorized document versions.
- **PR-5**: Answers and generated reports expose citations that resolve to authorized source chunks.
- **PR-6**: Insufficient evidence produces an explicit refusal or evidence-only response rather than unsupported output.
- **PR-7**: High-risk draft publication pauses for a human approval decision and resumes from persisted state.
- **PR-8**: Users can inspect run status, ordered events, attempts, errors, approvals, artifacts, and audit records.

### Reliability requirements

- **RR-1**: PostgreSQL is the source of truth for business state; Redis/Celery is a delivery and coordination layer.
- **RR-2**: Job creation and queue publication use a transactional Outbox.
- **RR-3**: Workers use atomic claim, per-job lease, heartbeat, and fencing tokens.
- **RR-4**: Duplicate API calls and duplicate queue delivery do not create duplicate effective side effects.
- **RR-5**: Retryable and permanent errors are classified; exhausted work enters a dead/manual-intervention state.
- **RR-6**: Worker termination, stale leases, Redis outages, model timeouts, and object-store failures have tests and operational handling.

### Security requirements

- **SR-1**: Every business record is tenant-scoped and every read/write revalidates the principal and resource ownership.
- **SR-2**: Prompt text, retrieved documents, and MCP results are treated as untrusted data.
- **SR-3**: Tools are classified by capability and enforced by server-side policy, not by prompt wording.
- **SR-4**: Publish or external-write tools require a valid approval bound to the exact target version.
- **SR-5**: Secrets, signed URLs, document bodies, and raw model output are excluded from normal logs and traces.

### Delivery requirements

- **DR-1**: Local development starts with documented commands and Docker Compose.
- **DR-2**: Pull requests run lint, type checks, tests, contracts, a small eval, image build, and security scans.
- **DR-3**: Kubernetes manifests support startup, liveness, readiness, graceful shutdown, migrations, and rollback.
- **DR-4**: A staging environment runs the real upload→ingestion→Agent smoke path.
- **DR-5**: The project produces a load-test report, a release record, and a rollback record.
- **DR-6**: All published metrics are labeled as targets or measured results; targets must never be presented as achieved measurements.
- **DR-7**: Every milestone produces a machine-readable evidence manifest that records the exact command or manual procedure, environment, commit SHA and image digest where applicable, result, artifact paths, limitations, and owner. Placeholder commands are not acceptable completion evidence.
- **DR-8**: External manual gates use a stable record containing gate ID, requirement, owner, blocking reason, prerequisites, required evidence, state, and review date. An open gate remains blocking and cannot be counted as completed work.
- **DR-9**: Production promotion is blocked until secret management, TLS/ingress and network boundaries, least-privilege service identities, compatible migrations, database backup/restore evidence, immutable image/SBOM/vulnerability evidence, audit access, monitoring/alerting, incident response, and rollback runbooks are reviewed.

## Milestone Children

| Child task | Responsibility |
|---|---|
| `m0-project-foundation` | Repository, local dependencies, health, config, baseline CI and observability |
| `m1-multipart-upload` | Upload sessions, direct Multipart transfer, resume, integrity and cleanup |
| `m2-durable-job-runtime` | Job/Attempt/Event/Outbox, Celery, claim, lease, heartbeat, retry and DLQ |
| `m3-document-rag` | Parsing, chunks, embeddings, hybrid retrieval, citations and eval |
| `m4-agent-mcp-hitl` | LangGraph, checkpointing, tools, MCP, SSE and human approval |
| `m5-observability-eval-load` | OTel, dashboards, safety/eval suites, load and fault injection |
| `m6-cicd-kubernetes` | Images, GitHub Actions, Kind/Kubernetes, staging, release and rollback |
| `m7-local-model-routing` | Provider gateway, vLLM, quantization, routing and benchmark report |

Dependencies are specified in each child's `implement.md`; tree order alone is not a dependency contract.

## Constraints

- Python 3.12 and FastAPI are the primary backend stack.
- React and TypeScript provide the operator UI.
- PostgreSQL + pgvector, Redis, Celery, and MinIO/S3 are required infrastructure.
- The first production shape is a modular monorepo, not many independently deployed microservices.
- The first Agent is a controlled workflow; arbitrary SQL, arbitrary code execution, and unrestricted network access are forbidden.
- Cloud accounts, public servers, and GPUs are external manual gates and cannot be marked complete without real evidence.
- Child tasks define their final executable test entry points once the relevant code exists; the parent evidence manifest indexes those real commands instead of retaining planning placeholders.

## Out of Scope

- Foundation-model pretraining.
- Production-grade OCR and complex table reconstruction.
- General-purpose code execution sandbox.
- Fully autonomous publication or irreversible external writes.
- Claims of production scale before measured tests exist.

## Acceptance Criteria

- [ ] All required M0-M7 child-task acceptance criteria are complete. Externally blocked work has a manual-gate record and remains open rather than being counted as complete; an optional milestone may be removed only by an explicit approved scope decision.
- [ ] A new environment can run the local stack and primary workflow from the README.
- [ ] The upload→ingestion→Agent→approval→artifact path passes E2E tests.
- [ ] Duplicate delivery, worker crash, stale lease, and expired fencing-token tests pass.
- [ ] Tenant leakage, prompt-injection write attempts, and unapproved publication tests are zero-tolerance gates.
- [ ] RAG and Agent eval datasets have versioned baseline results.
- [ ] A load report documents workload, P50/P95/P99, errors, resource saturation, bottleneck, and capacity conclusion.
- [ ] Pull-request CI blocks known failures and does not hide them through repeated reruns or allow-failure.
- [ ] Kubernetes staging completes the primary smoke path.
- [ ] At least one deployment and one rollback drill are recorded with immutable image digests.
- [ ] Each M0-M7 milestone has a reviewed evidence manifest; every required external gate is either satisfied with evidence or remains explicitly open and blocking.
- [ ] Production-readiness evidence covers secrets, TLS/network boundaries, service identity, migration compatibility, database backup/restore, image supply chain, audit access, monitoring/alerting, incident response, and rollback operations.
- [ ] Final documentation separates implemented facts, measured results, targets, known limitations, and future work.

## Notes

- The parent task is an integration and acceptance owner. Child tasks own implementation.
- The bootstrap-guidelines task remains open until M0 creates real code patterns that can be documented without inventing conventions.
