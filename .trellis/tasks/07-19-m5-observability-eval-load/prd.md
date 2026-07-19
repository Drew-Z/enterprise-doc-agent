# M5 Observability Evaluation And Load

## Goal

Turn the completed local upload -> ingestion -> grounded Agent -> approval ->
artifact workflow into a measurable system. M5 provides correlated traces,
bounded-cardinality metrics, reproducible quality and safety evaluations,
controlled fault experiments, and capacity reports tied to an exact environment
and commit.

M5 measures the existing system. It does not claim production scale, real-provider
answer quality, public deployment, or SLO attainment without matching evidence.

## Requirements

### Observability

- **M5-R1**: API, Outbox publication, Worker jobs, ingestion, retrieval, graph/model,
  MCP tool, approval, artifact, and object-store operations expose correlated traces
  or bounded metrics at their process boundary.
- **M5-R2**: Trace, log, and metric attributes exclude prompts, document text, model
  output, tool bodies, secrets, signed URLs, object keys, and unbounded tenant, actor,
  document, run, job, or event identifiers.
- **M5-R3**: Prometheus metrics cover API latency and errors, Outbox publication,
  Worker claims, job attempts and outcomes, and process health using finite labels.
- **M5-R4**: Local observability configuration is version controlled and telemetry
  failure is fail-open for the business workflow.

### Evaluation

- **M5-R5**: Versioned RAG evaluation reports retrieval and citation measures,
  authorization, refusal behavior, behavior versions, and limitations.
- **M5-R6**: Versioned Agent evaluation reports task outcome, tool and approval policy,
  citation validity, termination, and per-case failures.
- **M5-R7**: Safety evaluation is zero tolerance for cross-tenant access, prompt-
  injection write attempts, unapproved publication, and secret or raw-body leakage.
- **M5-R8**: Stream contracts verify ordered sequence, reconnect replay, terminal close,
  redaction, and recovery after an interrupted client connection.

### Load And Reliability

- **M5-R9**: A reproducible local load runner covers health, create/status APIs,
  idempotent duplicates, polling, and an explicitly bounded end-to-end scenario.
- **M5-R10**: Reports contain workload, P50/P95/P99, throughput, errors, observed
  saturation data, bottleneck, capacity conclusion, and target-versus-measured fields.
- **M5-R11**: Controlled local/test experiments cover handler failure or delay, stale
  lease behavior, model timeout, MCP failure, and object-store failure without exposing
  a tenant-controlled fault switch.
- **M5-R12**: Fault injection is disabled by default and rejected outside local/test.

### Evidence

- **M5-R13**: M5 stores sanitized machine-readable reports and immutable manifests
  under `evidence/m5/` and updates `evidence/index.json`.
- **M5-R14**: Every result records command or procedure, environment, timestamps,
  commit, artifact hashes, limitations, and owner. External-only gates remain
  `blocked_external` and never count as passed.

## Acceptance Criteria

- [x] API and Worker expose Prometheus-compatible metrics from process-local registries.
- [x] Metric names, units, buckets, finite labels, and redaction are contract tested.
- [x] API metrics use route templates and never raw identifier-bearing paths.
- [x] Worker metrics distinguish claim, success, retry/failure, cancellation, and
  Outbox publication outcomes without UUID labels.
- [x] Versioned RAG, Agent, safety, and stream commands emit machine-readable reports.
- [x] Safety cases produce zero unauthorized reads and zero forbidden side effects.
- [x] Local load reports include P50/P95/P99, errors, throughput, sample count,
  environment, bottleneck, and a non-production capacity conclusion.
- [x] Deterministic fault scenarios are local/test-only and prove stable recovery or
  stable terminal behavior.
- [x] Existing M0-M4 quality, integration, evaluation, and browser gates remain green.
- [x] M5 manifest and index validate; targets are never reported as measured facts.
- [x] Real-provider quality and representative production capacity remain explicit
  manual gates when credentials, hardware, or a dedicated environment are unavailable.

## Out Of Scope

- Production SLO declarations, public traffic, pager integration, Kubernetes/CD,
  managed-cloud durability, and real GPU/provider benchmarking.
