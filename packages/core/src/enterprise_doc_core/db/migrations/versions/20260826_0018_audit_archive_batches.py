"""Add immutable audit archive batch receipts.

Revision ID: 20260826_0018
Revises: 20260825_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0018"
down_revision = "20260825_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_archive_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_event_count", sa.Integer(), nullable=False),
        sa.Column("archived_event_ids", sa.JSON(), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "fingerprint",
            name="uq_audit_archive_batches_tenant_fingerprint",
        ),
    )
    op.create_index(
        "ix_audit_archive_batches_tenant_created_at",
        "audit_archive_batches",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_archive_batches_tenant_created_at", table_name="audit_archive_batches")
    op.drop_table("audit_archive_batches")
