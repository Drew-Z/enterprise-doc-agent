# M3 Document Ingestion and Hybrid RAG

## Goal

Consume a completed `DocumentVersion` through the M2 durable job runtime and produce
versioned, tenant-scoped chunks and embeddings that support grounded hybrid retrieval.
Every returned citation must resolve to an authorized chunk from the requested document
version; insufficient evidence must produce a refusal instead of an invented answer.

## Requirements

### Ingestion

- Read the object through bounded range requests into a controlled temporary spool; do
  not load an unbounded document into process memory.
- Parse TXT, PDF, and DOCX while retaining page number where available, heading, byte or
  character offsets, chunk index, source filename, and document version.
- Normalize text deterministically, split by headings/paragraphs with bounded size and
  overlap, and compute a content hash for every chunk.
- Make ingestion resumable and idempotent by `(document_version_id, parser_version,
  chunker_version, embedding_version)`; reruns replace only the same generation.
- Mark the DocumentVersion `ready` only after all chunks and embeddings are committed;
  deterministic failures mark it `failed` with a stable error code.

### Retrieval

- Store PostgreSQL full-text search and pgvector embeddings with tenant and version
  filters. Vector similarity alone is insufficient for legal terms, identifiers, and
  clause numbers.
- Run keyword and vector recall independently, deduplicate, combine with deterministic
  Reciprocal Rank Fusion (RRF), and apply bounded deterministic relevance gates. A
  learned or cross-encoder reranker is explicitly out of M3 scope.
- Refuse when the authorized evidence score or candidate count is below threshold.
- Return citations containing chunk id, document version id, filename, page/offset,
  heading, and a short evidence excerpt. Citation validation must run against the
  authorized candidate set, not merely model-produced ids.
- Support index-generation metadata so a new embedding model can be built beside the
  current generation and switched atomically after completeness and embedding
  integrity checks. Offline quality evaluation is a separate gate and is not yet wired
  into activation.

### Security and tenancy

- Every chunk, retrieval query, and citation check is tenant-scoped. ACL filtering occurs
  before evidence is exposed to the model.
- Treat document text as untrusted input. Parsing must not execute macros, external
  references, or instructions embedded in the document.
- Do not log document text, embeddings, raw query bodies, or signed object URLs.

## Acceptance Criteria

- [x] Add chunk, ingestion-generation, and embedding schema with tenant/version foreign
  keys, deterministic hashes, FTS/vector indexes, nullable pre-embedding checkpoints,
  count constraints, and migration downgrade tests.
- [x] Unit tests cover TXT/PDF/DOCX parsing, malformed input, page/heading/offset
  metadata, bounded spool reads, chunk boundaries, and deterministic reruns.
- [x] M2 integration proves `document.ingest` advances `uploaded -> ready` exactly once,
  resumes an embedding failure from persisted chunks without another object download,
  rejects corrupt checkpoints, and records stable failure state without duplicate rows.
- [x] Hybrid retrieval tests prove keyword-only terms, vector-only paraphrases, RRF
  ordering, tenant/version isolation, deduplication, and index-generation switching.
- [x] Citation tests reject unauthorized, wrong-version, missing, and out-of-candidate
  citations; valid citations resolve to stored chunks with stable metadata.
- [x] Refusal tests cover empty evidence, insufficient candidates, low score, and
  conflicting versions; M3 returns a refusal decision and has no LLM answer path.
- [x] A versioned live PostgreSQL retrieval set reports Recall@K, MRR/nDCG and refusal
  precision/recall from real service output. A separate deterministic citation-gate
  fixture reports citation precision; M3 does not claim answer-generation quality.
- [x] Existing M0-M2 tests, CI, evidence contracts, and M1 artifacts remain unchanged.

## Notes

- Keep parser/retrieval logic in `packages/core`; API and Worker remain adapters.
- Use an embedding protocol and deterministic local test provider; do not claim real model
  quality or production vector capacity without a measured run.
- Applied migrations and prior evidence are immutable.
