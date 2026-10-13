"""Add independent analysis projects and immutable source manifests."""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261011_0039"
down_revision = "20261011_0038"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "analysis_projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("draft_spec", JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_project_scope"),
        sa.CheckConstraint("draft_revision > 0", name="revision_positive"),
        sa.CheckConstraint("jsonb_typeof(draft_spec) = 'object'", name="spec_object"),
    )
    op.create_index("ix_analysis_projects_tenant_id", "analysis_projects", ["tenant_id"])
    op.create_table(
        "analysis_source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("project_revision", sa.Integer(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_snapshot_scope"),
        sa.UniqueConstraint("project_id", "project_revision", name="uq_analysis_snapshot_revision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["analysis_projects.tenant_id", "analysis_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("project_revision > 0", name="revision_positive"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )
    op.create_index(
        "ix_analysis_source_snapshots_tenant_id", "analysis_source_snapshots", ["tenant_id"]
    )
    op.create_table(
        "analysis_snapshot_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id", "document_version_id", name="uq_analysis_source_version"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["analysis_source_snapshots.tenant_id", "analysis_source_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "generation_id"],
            [
                "document_ingestion_generations.tenant_id",
                "document_ingestion_generations.document_version_id",
                "document_ingestion_generations.id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_analysis_snapshot_sources_snapshot_id", "analysis_snapshot_sources", ["snapshot_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE analysis_projects, analysis_source_snapshots, "
            "analysis_snapshot_sources IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM analysis_projects)")):
        raise RuntimeError("analysis_history_present")
    op.drop_table("analysis_snapshot_sources")
    op.drop_table("analysis_source_snapshots")
    op.drop_table("analysis_projects")
