from __future__ import annotations

from enterprise_doc_core.db import Base
from enterprise_doc_core.documents.models import (
    DEFAULT_EMBEDDING_DIMENSION,
    DocumentChunk,
    DocumentIngestionGeneration,
)


def test_m3_models_are_registered_and_tenant_scoped() -> None:
    assert DocumentIngestionGeneration.__tablename__ == "document_ingestion_generations"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert {"document_ingestion_generations", "document_chunks"} <= set(Base.metadata.tables)

    for table_name in ("document_ingestion_generations", "document_chunks"):
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns
        assert "document_version_id" in table.columns


def test_m3_generation_has_version_and_active_constraints() -> None:
    table = Base.metadata.tables["document_ingestion_generations"]
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}

    assert "uq_document_ingestion_generations_version_key" in constraints
    assert "ck_document_ingestion_generations_status_valid" in constraints
    assert "ck_document_ingestion_generations_stage_valid" in constraints
    assert "uq_document_ingestion_generations_active_version" in indexes


def test_m3_chunks_have_hybrid_search_indexes_and_fixed_embedding_contract() -> None:
    table = Base.metadata.tables["document_chunks"]
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}

    assert "uq_document_chunks_generation_index" in constraints
    assert "ck_document_chunks_chunk_index_non_negative" in constraints
    assert "ck_document_chunks_offsets_valid" in constraints
    assert {"ix_document_chunks_fts", "ix_document_chunks_embedding_hnsw"} <= indexes
    assert table.columns.embedding.type.dim == DEFAULT_EMBEDDING_DIMENSION
