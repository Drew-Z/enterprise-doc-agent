"""Add tenant identity, multipart upload, and document version tables.

Revision ID: 20260717_0002
Revises: 20260717_0001
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260717_0002"
down_revision = "20260717_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "used_storage_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_storage_bytes",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quota_bytes > 0", name="quota_bytes_positive"),
        sa.CheckConstraint(
            "used_storage_bytes >= 0 AND reserved_storage_bytes >= 0",
            name="storage_counters_non_negative",
        ),
        sa.CheckConstraint(
            "used_storage_bytes + reserved_storage_bytes <= quota_bytes",
            name="storage_within_quota",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('owner', 'member')", name="role_valid"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            name="uq_memberships_tenant_id_user_id",
        ),
    )
    op.create_index(
        "ix_memberships_user_id_is_active",
        "memberships",
        ["user_id", "is_active"],
        unique=False,
    )
    op.create_table(
        "upload_sessions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("pending_document_id", sa.Uuid(), nullable=False),
        sa.Column("pending_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("object_store_upload_id", sa.String(length=512), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("declared_media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=False),
        sa.Column("part_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_part_count", sa.Integer(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completion_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aborted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_part_count BETWEEN 1 AND 10000",
            name="expected_part_count_range",
        ),
        sa.CheckConstraint(
            "part_size_bytes > 0",
            name="part_size_positive",
        ),
        sa.CheckConstraint(
            "reserved_bytes >= 0",
            name="reserved_bytes_non_negative",
        ),
        sa.CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        sa.CheckConstraint(
            "status IN ('initializing', 'active', 'completing', 'completed', "
            "'aborted', 'expired', 'failed')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_upload_sessions_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_upload_sessions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_upload_sessions"),
        sa.UniqueConstraint("object_key", name="uq_upload_sessions_object_key"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_upload_sessions_tenant_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_upload_sessions_tenant_id_status_expires_at",
        "upload_sessions",
        ["tenant_id", "status", "expires_at"],
        unique=False,
    )
    op.create_table(
        "documents",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_documents_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_documents_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index(
        "ix_documents_tenant_id_created_at",
        "documents",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "upload_parts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("expected_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("observed_checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "part_number BETWEEN 1 AND 10000",
            name="part_number_range",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="size_bytes_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_upload_parts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["upload_sessions.id"],
            name="fk_upload_parts_upload_session_id_upload_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_upload_parts"),
        sa.UniqueConstraint(
            "upload_session_id",
            "part_number",
            name="uq_upload_parts_upload_session_id_part_number",
        ),
    )
    op.create_index(
        "ix_upload_parts_tenant_id_upload_session_id",
        "upload_parts",
        ["tenant_id", "upload_session_id"],
        unique=False,
    )
    op.create_table(
        "document_versions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_media_type", sa.String(length=128), nullable=False),
        sa.Column("detected_media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_sha256_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transport_checksum_sha256", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="size_bytes_positive",
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'ready', 'failed')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="version_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_document_versions_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_document_versions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["upload_sessions.id"],
            name="fk_document_versions_upload_session_id_upload_sessions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_id_version_number",
        ),
        sa.UniqueConstraint("object_key", name="uq_document_versions_object_key"),
        sa.UniqueConstraint(
            "upload_session_id",
            name="uq_document_versions_upload_session_id",
        ),
    )
    op.create_index(
        "ix_document_versions_tenant_id_status_created_at",
        "document_versions",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_versions_tenant_id_status_created_at",
        table_name="document_versions",
    )
    op.drop_table("document_versions")
    op.drop_index("ix_upload_parts_tenant_id_upload_session_id", table_name="upload_parts")
    op.drop_table("upload_parts")
    op.drop_index("ix_documents_tenant_id_created_at", table_name="documents")
    op.drop_table("documents")
    op.drop_index(
        "ix_upload_sessions_tenant_id_status_expires_at",
        table_name="upload_sessions",
    )
    op.drop_table("upload_sessions")
    op.drop_index("ix_memberships_user_id_is_active", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")
