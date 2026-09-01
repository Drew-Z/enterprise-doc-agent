# Enterprise Document Agent: Current Architecture and Configuration Gap

Date: 2026-08-27

This note records what the repository actually implements. It deliberately
separates runnable deterministic fixtures from production-grade model-backed
capabilities. No secret values, document text, object keys, or user identifiers
belong in this file.

## Short Answer

This is a combined system:

1. An enterprise document ingestion and RAG platform.
2. A LangGraph-based Agent workflow that calls the RAG capability through a
   signed MCP tool boundary, freezes evidence, generates a grounded answer,
   and applies approval/publish policy.

The default local profile is not a fully model-backed semantic RAG deployment.
The chat gateway and embedding pipeline can use OpenAI-compatible providers,
but staging still requires protected endpoint, model and key configuration.
Local/test defaults use deterministic hash embeddings; the reviewed real-provider
shape is 1024 dimensions. There is no independent cross-encoder reranker.

## Actual Data Path

```text
upload API
  -> object store multipart upload
  -> complete transaction: document/version + document.ingest job + outbox
  -> publisher -> Redis/Celery document-ingestion queue
  -> consumer
  -> download -> TXT/DOCX/PDF parse -> section chunks
  -> PostgreSQL tsvector + pgvector persistence
  -> configured hash or OpenAI-compatible embedding
  -> activate generation and mark version ready

Agent run
  -> authorize tenant/user and run
  -> MCP search_document
  -> keyword + vector recall -> RRF fusion -> evidence threshold/refusal
  -> freeze authorized evidence in AgentRunEvidence
  -> build grounded model request
  -> deterministic or OpenAI-compatible chat gateway
  -> citation/grounding validation
  -> draft -> risk/approval interrupt -> publish or reject
```

Primary code evidence:

- Upload completion creates the asynchronous ingestion job and outbox event:
  `packages/core/src/enterprise_doc_core/uploads/session_service.py:1102-1151`.
- Parsing and chunking support TXT, DOCX, and PDF; the ingestion stages are
  implemented in `packages/core/src/enterprise_doc_core/documents/ingestion.py:163-237`
  and `packages/core/src/enterprise_doc_core/documents/ingestion_service.py:181-239`.
- Keyword and vector retrieval are fused with reciprocal-rank fusion in
  `packages/core/src/enterprise_doc_core/documents/retrieval_service.py:62-140`
  and `packages/core/src/enterprise_doc_core/documents/retrieval.py:58-169`.
- The Agent graph and approval branches are in
  `packages/core/src/enterprise_doc_core/agents/graph.py:231-391`.
- The Agent backend calls MCP `search_document`, freezes evidence, and builds a
  grounded request in
  `apps/worker/src/enterprise_doc_worker/agent_backend.py:182-235`.
- MCP runtime wiring is in `apps/mcp/src/enterprise_doc_mcp/server.py:104-145,237-261`.

## What Is Real Today

### RAG

- PostgreSQL persistence, full-text `tsvector` search, pgvector column and
  tenant/version authorization checks are real code paths.
- Hybrid recall is real: keyword recall and vector recall are combined with
  RRF. A low-evidence/low-relevance result can be refused instead of answered
  from model memory.
- Citation validation checks that returned evidence belongs to the authorized
  tenant/version/candidate set.

### Agent

- The workflow is a durable, stateful graph, not a single prompt endpoint.
- MCP capability checks, signed execution context, evidence freezing, draft,
  approval, and publish boundaries are implemented.
- The deterministic gateway is useful for repeatable tests and CI.
- An OpenAI-compatible chat gateway exists and validates/repairs structured
  output, but it is only usable after a real route is configured.

### Operations

- Upload-to-ingestion is asynchronous through an outbox publisher and a
  separate Celery consumer.
- Jobs have leases, heartbeats, retry/dead states, and attempt history.
- Kubernetes manifests, image digests, deployment smoke, SBOM/provenance and
  signing workflows exist. The reviewed `single-node-4c4g` profile is the
  current bounded staging target; `single-node-4c8g` remains a separate,
  higher-memory capacity-drill profile, and `tiny-single-node` remains a
  readiness/isolated-probe baseline.

### Identity and governance

- Tenant membership administration is owner-only and protects the last active
  owner. Provisioning, role changes, deactivation/reactivation, explicit
  issuer/subject bindings, and lifecycle audit events are implemented.
- Document visibility supports `tenant` and `restricted` modes with user and
  tenant-role grants. The same server-side predicate is applied to inventory,
  retrieval, Agent runs/tools, run events, and artifact access.
- Audit events support tenant-scoped query/export plus retention, legal-hold,
  and non-destructive archive governance controls. WORM storage, deletion
  proof, and independent archive recovery remain production gates.
- Local JWT sessions include a required `jti`; `POST /api/session/logout`
  records tenant-scoped revocation, writes an `auth.session.revoked` audit
  event, and supports bounded expiry cleanup. External OIDC token revocation
  remains an IdP integration gate.
- A restricted tenant-token SCIM contract now includes ServiceProviderConfig,
  ResourceTypes and Schemas discovery, tenant-scoped Users pagination and
  `userName`/`externalId` equality filters, a bounded sequential Bulk endpoint
  for User `POST`/`PUT`/`PATCH`/`DELETE` operations, alongside single-user
  GET/upsert/deprovision and a bounded PATCH limited to `replace` of `active`
  or `userName`. It is intentionally not a complete SCIM server or IdP
  integration.

## What Is Still a Deterministic Fixture or Missing

### Embedding

The worker and MCP retrieval runtime now call `build_embedding_provider` with
the shared `EmbeddingSettings`. The factory supports the deterministic hash
provider for local/test runs and an OpenAI-compatible provider with bounded
batching, retries, timeout handling, response validation, model identity and
dimension checks. The reviewed database shape is `vector(1024)` after the
versioned embedding migration and reindex workflow documented in
`docs/ops/real-embedding-rollout.md`.

The default hash provider remains deterministic and is rejected outside
local/test environments. A real staging run therefore requires an operator
supplied HTTPS embedding endpoint, model name and API key through the
protected secret channel. Local provider probes and reviewed 4B vector reports
demonstrate adapter and migration behavior, but do not prove semantic quality,
production capacity or provider availability.

### Reranking

RRF is an algorithmic fusion step, not a cross-encoder or second LLM reranker.
No independent reranker implementation or configuration was found. Improving
recall quality with a real embedding model will not, by itself, add reranking.

### Agent model

The supported providers are only `deterministic` and `openai_compatible`:
`packages/core/src/enterprise_doc_core/config/settings.py:18-20`.
The deterministic provider is rejected outside local/test at
`packages/core/src/enterprise_doc_core/config/settings.py:257-264`.

The staging overlay currently contains placeholder OpenAI-compatible values in
`infra/k8s/overlays/staging/configmap-patch.yaml:12-14`; it is not a real model
configuration until protected deployment variables and the Kubernetes secret
are supplied.

### Previously observed blocker

The earlier fresh-process `Base.metadata` registration defect is fixed. The
database package now exposes `register_models`, the session-factory path loads
the complete model registry, and
`packages/core/tests/test_db.py::test_production_session_factory_registers_all_foreign_key_targets`
guards the worker-style import path. Current ingestion or staging failures
must therefore be diagnosed from the live dependency, model, object-store and
deployment evidence rather than attributed to the old metadata defect.

## Information You Need to Decide or Supply

### A. Chat/Agent model route (required for staging outside local/test)

Provide through the protected GitHub Environment/Kubernetes secret channel,
not in chat or committed files:

- OpenAI-compatible HTTPS base URL, normally ending in `/v1`.
- Exact chat model name.
- API key secret for `MODEL__API_KEY`.

Recommended optional non-secret values:

- model revision or deployment revision;
- context window;
- route identifier and request/route deadlines;
- fallback route, only if its failure semantics and budget are understood.

The endpoint must support the request/response shape expected by
`OpenAICompatibleChatGateway` and return structured output that passes the
grounding schema. A model name alone is not enough.

### B. Embedding rollout decision (required before claiming semantic RAG)

Choose one of these explicit paths:

1. **Deterministic local/test path:** keep the hash provider for repeatable
   authorization, retry, and deployment tests. It must remain labelled as a
   non-semantic fixture and is rejected in staging/production settings.
2. **Reviewed real-provider path:** configure the OpenAI-compatible 1024-
   dimensional route, run the versioned reindex, and preserve the provider
   identity, migration, readiness and quality evidence. The adapter and
   migration are implemented; endpoint ownership, quotas, semantic quality and
   representative capacity still require external validation.

### C. Retrieval quality policy (recommended)

Decide whether the next milestone needs:

- a cross-encoder/LLM reranker;
- query rewrite/expansion;
- per-document or per-tenant retrieval policies;
- evaluation data and target recall/precision/grounded-answer thresholds;
- citation display and refusal UX.

These are product/quality decisions, not deployment secrets.

### D. Operational inputs still missing for a real staging gate

- approved model base URL and model name;
- database egress allowlist/CIDR and pooler connection budget;
- object-store HTTPS endpoints and allowed host variables;
- protected staging kubeconfig/context/namespace values;
- smoke JWT secret and TLS secret name;
- immutable image digests for the four workloads;
- approval that external model and object-store data may receive the selected
  document class;
- rollback/recovery expectations and a test document that contains no sensitive
  business data.

Do not paste any of the secret values into this repository or into a chat
message. Use the protected environment workflow and record only redacted
presence/validation evidence.

## Recommended Order

1. Keep the local regression, migration, authorization, audit, SCIM and local
   JWT revocation contracts green; the current non-integration suite is
   `989 passed` (`125` integration tests deselected).
2. Supply protected chat and embedding provider configuration, then run the
   reviewed 4C4G staging rollout with readiness and authenticated business
   smoke evidence.
3. Measure real-provider retrieval quality and cost on the representative
   corpus before changing prompts, reranking or refusal thresholds.
4. Complete the external IdP/SCIM, full ABAC/PDP, WORM/archive, managed
   observability and production export reviews as separate gates.
5. Treat the result as a bounded single-node 4C4G staging drill until capacity,
   HA, restore/RPO/RTO and rollback evidence exist. The host has no standby
   node, so this does not establish node-failure recovery or multi-fault-domain
   disaster recovery.

## Release Boundary

Even after a successful smoke run, the evidence must say:

- single-node 4C4G staging only (the 2C2G profile is readiness-only);
- no production capacity proof;
- no multi-node HA;
- no GPU/vLLM validation;
- no managed observability proof;
- no real disaster-recovery RPO/RTO proof.
