# M3 Document Ingestion and Hybrid RAG Design

## Data flow

`document.ingest` is claimed by M2 and runs a stage machine:

```text
download_spool -> parse -> chunk -> embed -> index -> ready
```

Each stage writes a bounded `stage` marker in the ingestion generation row. The durable
resume boundary is `embed`: chunks with NULL embeddings and `stage=embed` are committed
in one transaction. A retry validates indexes, hashes, counts, and NULL vectors, then
embeds those rows in place without downloading or parsing the object again. Earlier
failures restart from `download_spool`; the marker alone is not treated as an artifact
checkpoint.

## Storage model

- `document_ingestion_generations`: tenant, document version, parser/chunker/embedding
  versions, status, current stage, counts, error code, timestamps, and an active marker.
- `document_chunks`: tenant, document version, generation, chunk index, heading, page,
  start/end offsets, normalized text, SHA-256, `tsvector`, and a nullable vector until
  the embedding checkpoint completes.

The active generation is selected by `(tenant_id, document_version_id, active=true)`;
an index-generation switch is a single PostgreSQL update after evaluation. Chunks from
old generations remain available for rollback until retention removes them.

## Parsing and bounded I/O

The Worker asks `head_object` for size, then reads fixed-size ranges into a
`SpooledTemporaryFile` with a configured maximum. TXT is decoded as UTF-8. PDF uses a
page-aware reader and DOCX uses `python-docx` without executing macros or external
links. Parser output is a normalized list of page/section records; the chunker never
sees object-store credentials or paths.

## Chunking and embeddings

Chunking first respects headings and paragraphs, then applies a hard character/token
bound with small overlap. Every chunk has a deterministic hash and stable index within a
generation. `EmbeddingProvider` is a protocol; tests use a deterministic hash provider,
while a real provider is a later model-routing decision. Embedding dimensions and model
version are stored with the generation and checked before insert.

## Hybrid retrieval and citation gate

Keyword recall uses PostgreSQL `websearch_to_tsquery`/`ts_rank_cd`; vector recall uses
cosine distance with tenant, document-version, ready/succeeded generation, and active
generation filters. Vector candidates must also pass an absolute distance ceiling. The
two ranked lists are deduplicated and combined with
`score = sum(1/(rrf_k + rank))`. This is deterministic fusion, not a learned reranker.
The service applies topK, candidate-count, RRF-floor, and vector-distance gates, then
validates every citation against the final authorized candidate map. PostgreSQL's
`simple` configuration is only a deterministic baseline and is not evidence of Chinese
tokenization quality. No LLM call is required for M3; M4 may pass validated evidence to
a model.

## Evaluation boundary

The default evaluation command seeds an isolated PostgreSQL tenant with deterministic
chunks and vectors, invokes `HybridRetrievalService`, and records ranked ids, scores,
refusal reasons, dataset hash, migration revision, PostgreSQL version, and pgvector
version. It is a retrieval regression for SQL, pgvector, fusion, and refusal behavior.
It does not measure production embedding semantics, Chinese tokenization, generated
answers, or citations. Citation precision remains a separate validator fixture until an
answer/citation generator exists.

## Failure and security boundaries

Malformed files produce stable parser errors and a failed generation; they do not change
another document version. A document can contain prompt-injection text, but parsing and
retrieval treat it as data. ACL/tenant filters are SQL predicates before candidate
construction, and citation excerpts are bounded. Logs contain ids and error classes only.
