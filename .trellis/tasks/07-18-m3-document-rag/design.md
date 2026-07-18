# M3 Document Ingestion and Hybrid RAG Design

## Data flow

`document.ingest` is claimed by M2 and runs a stage machine:

```text
download_spool -> parse -> chunk -> embed -> index -> ready
```

Each stage writes a bounded `stage`/`stage_version` marker in the ingestion generation
row and is safe to repeat. A failed generation remains inspectable; retry reuses already
committed deterministic stages and only replaces the incomplete generation.

## Storage model

- `document_ingestion_generations`: tenant, document version, parser/chunker/embedding
  versions, status, current stage, counts, error code, timestamps, and an active marker.
- `document_chunks`: tenant, document version, generation, chunk index, heading, page,
  start/end offsets, normalized text, SHA-256, `tsvector`, and vector embedding.

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
cosine distance with tenant, document-version, and active-generation filters. The two
ranked lists are deduplicated and combined with `score = sum(1/(rrf_k + rank))`.
The service applies a deterministic topK/score threshold and validates every citation
against the final authorized candidate map before returning it. No LLM call is required
for M3; M4 may pass the validated evidence to a model.

## Failure and security boundaries

Malformed files produce stable parser errors and a failed generation; they do not change
another document version. A document can contain prompt-injection text, but parsing and
retrieval treat it as data. ACL/tenant filters are SQL predicates before candidate
construction, and citation excerpts are bounded. Logs contain ids and error classes only.
