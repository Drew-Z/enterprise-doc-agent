# M3 Document Ingestion and Hybrid RAG Implementation Plan

## Slice 1: Schema and parser contracts

- [x] Add document ingestion generation/chunk models and additive migration with FTS and
  pgvector indexes.
- [x] Add parser, bounded object spool, normalized page/section records, and deterministic
  chunker contracts with unit tests.
- [x] Add embedding protocol plus deterministic hash provider for local tests.

## Slice 2: Durable ingestion handler

- [x] Register a `document.ingest` handler through M2's consumer factory.
- [x] Implement stage checkpoints, idempotent generation replacement, DocumentVersion
  `ready/failed` transitions, and retry-safe errors.
- [x] Add PostgreSQL integration tests for retry/resume and one effective generation.

## Slice 3: Hybrid retrieval and citation gate

- [x] Implement keyword/vector dual recall with SQL tenant/version filters, RRF, score
  threshold, deduplication, and deterministic result ordering.
- [x] Implement citation resolution, authorization, bounded excerpts, and refusal result.
- [x] Add unit/integration tests for ACL leakage, wrong-version citations, and refusal.

## Slice 4: Evaluation and evidence

- [x] Add a small versioned QA/refusal dataset with golden chunks and a reproducible eval
  command reporting Recall@K, MRR/nDCG, citation precision, and refusal metrics.
- [x] Run M0-M2 regression gates, M3 migration/integration tests, and evidence contract.
- [x] Update the interview document with code-backed M3 facts and explicit model-quality
  limitations; commit without archiving until reviewed.
