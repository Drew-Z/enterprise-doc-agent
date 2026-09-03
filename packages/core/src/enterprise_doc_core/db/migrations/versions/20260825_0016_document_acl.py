"""Add opt-in document visibility and document-scoped grants.

Revision ID: 20260825_0016
Revises: 20260825_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20260825_0016"
down_revision = "20260825_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("access_mode", sa.String(length=20), server_default="tenant", nullable=False),
    )
    op.create_check_constraint(
        "document_access_mode_valid",
        "documents",
        "access_mode IN ('tenant', 'restricted')",
    )
    op.create_unique_constraint("uq_documents_tenant_id_id", "documents", ["tenant_id", "id"])
    op.create_index("ix_documents_tenant_id_access_mode", "documents", ["tenant_id", "access_mode"])
    op.create_table(
        "document_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_user_id", sa.Uuid(), nullable=True),
        sa.Column("grantee_role", sa.String(length=20), nullable=True),
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
            "(grantee_user_id IS NOT NULL AND grantee_role IS NULL) OR "
            "(grantee_user_id IS NULL AND grantee_role IS NOT NULL)",
            name="document_grant_single_target",
        ),
        sa.CheckConstraint(
            "grantee_role IS NULL OR grantee_role IN ('owner', 'member')",
            name="document_grant_role_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_document_grants_tenant_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "grantee_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="CASCADE",
            name="fk_document_grants_tenant_user",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "grantee_user_id", name="uq_document_grants_document_user"
        ),
        sa.UniqueConstraint("document_id", "grantee_role", name="uq_document_grants_document_role"),
    )
    op.create_index(
        "ix_document_grants_tenant_document", "document_grants", ["tenant_id", "document_id"]
    )
    op.create_index("ix_document_grants_user", "document_grants", ["tenant_id", "grantee_user_id"])
    op.create_index("ix_document_grants_role", "document_grants", ["tenant_id", "grantee_role"])


def downgrade() -> None:
    op.drop_index("ix_document_grants_role", table_name="document_grants")
    op.drop_index("ix_document_grants_user", table_name="document_grants")
    op.drop_index("ix_document_grants_tenant_document", table_name="document_grants")
    op.drop_table("document_grants")
    op.drop_index("ix_documents_tenant_id_access_mode", table_name="documents")
    op.drop_constraint("document_access_mode_valid", "documents", type_="check")
    op.drop_constraint("uq_documents_tenant_id_id", "documents", type_="unique")
    op.drop_column("documents", "access_mode")
