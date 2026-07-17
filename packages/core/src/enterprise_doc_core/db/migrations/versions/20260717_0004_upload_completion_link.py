"""Add the durable upload completion link.

Revision ID: 20260717_0004
Revises: 20260717_0003
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260717_0004"
down_revision = "20260717_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_upload_sessions_document_version_id",
        "upload_sessions",
        ["document_version_id"],
    )
    op.create_foreign_key(
        "fk_upload_sessions_document_version_id_document_versions",
        "upload_sessions",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_upload_sessions_document_version_id_document_versions",
        "upload_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_upload_sessions_document_version_id",
        "upload_sessions",
        type_="unique",
    )
    op.drop_column("upload_sessions", "document_version_id")
