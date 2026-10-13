"""Preserve immutable, source-bound document structure for parser v2."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261011_0038"
down_revision = "20261010_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ingestion_generation_scope",
        "document_ingestion_generations",
        ["tenant_id", "document_version_id", "id"],
    )
    op.create_table(
        "document_structures",
        sa.Column("generation_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("coverage", sa.String(20), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "generation_id"],
            [
                "document_ingestion_generations.tenant_id",
                "document_ingestion_generations.document_version_id",
                "document_ingestion_generations.id",
            ],
            name="fk_document_structures_generation_scope",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("coverage IN ('complete','partial','unknown')", name="coverage_valid"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )
    op.create_index("ix_document_structures_tenant_id", "document_structures", ["tenant_id"])
    op.create_index(
        "ix_document_structures_document_version_id", "document_structures", ["document_version_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE document_structures IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM document_structures)")):
        raise RuntimeError("document_structure_history_present")
    op.drop_table("document_structures")
    op.drop_constraint(
        "uq_ingestion_generation_scope", "document_ingestion_generations", type_="unique"
    )
