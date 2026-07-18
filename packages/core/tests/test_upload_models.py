from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.db import Base, create_session_factory
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.uploads.models import UploadPart, UploadSession


def test_m1_metadata_contains_tenant_scoped_business_tables() -> None:
    assert {
        "document_versions",
        "documents",
        "memberships",
        "tenants",
        "upload_parts",
        "upload_sessions",
        "users",
    } <= set(Base.metadata.tables)

    for table_name in (
        "documents",
        "document_versions",
        "memberships",
        "upload_parts",
        "upload_sessions",
    ):
        assert "tenant_id" in Base.metadata.tables[table_name].columns


def test_m1_models_have_stable_table_ownership() -> None:
    assert Tenant.__tablename__ == "tenants"
    assert User.__tablename__ == "users"
    assert Membership.__tablename__ == "memberships"
    assert Document.__tablename__ == "documents"
    assert DocumentVersion.__tablename__ == "document_versions"
    assert UploadSession.__tablename__ == "upload_sessions"
    assert UploadPart.__tablename__ == "upload_parts"
    assert "document_version_id" in Base.metadata.tables["upload_sessions"].columns
    assert "cleanup_claimed_at" in Base.metadata.tables["upload_sessions"].columns
    assert "cleanup_claim_token" in Base.metadata.tables["upload_sessions"].columns
    assert "observation_version" in Base.metadata.tables["upload_parts"].columns
    assert "observed_at" in Base.metadata.tables["upload_parts"].columns


def test_m1_metadata_has_named_idempotency_and_version_constraints() -> None:
    upload_constraints = {
        constraint.name for constraint in Base.metadata.tables["upload_sessions"].constraints
    }
    version_constraints = {
        constraint.name for constraint in Base.metadata.tables["document_versions"].constraints
    }

    assert "uq_upload_sessions_tenant_id_idempotency_key" in upload_constraints
    assert "uq_upload_sessions_document_version_id" in upload_constraints
    assert "uq_document_versions_upload_session_id" in version_constraints
    assert "uq_document_versions_document_id_version_number" in version_constraints


def test_quota_and_upload_state_constraints_are_named() -> None:
    tenant_constraints = {
        constraint.name for constraint in Base.metadata.tables["tenants"].constraints
    }
    upload_constraints = {
        constraint.name for constraint in Base.metadata.tables["upload_sessions"].constraints
    }

    assert {
        "ck_tenants_quota_bytes_positive",
        "ck_tenants_storage_counters_non_negative",
        "ck_tenants_storage_within_quota",
    } <= tenant_constraints
    assert {
        "ck_upload_sessions_expected_part_count_range",
        "ck_upload_sessions_part_size_positive",
        "ck_upload_sessions_reserved_bytes_non_negative",
        "ck_upload_sessions_cleanup_claim_pair",
        "ck_upload_sessions_size_bytes_positive",
        "ck_upload_sessions_status_valid",
    } <= upload_constraints


def test_session_factory_uses_async_sessions_without_expiring_committed_models() -> None:
    factory = create_session_factory(None)

    assert isinstance(factory, async_sessionmaker)
    assert factory.class_ is AsyncSession
    assert factory.kw["expire_on_commit"] is False
