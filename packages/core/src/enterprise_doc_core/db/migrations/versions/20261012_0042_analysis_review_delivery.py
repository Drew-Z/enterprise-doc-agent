"""Add immutable analysis cell revisions and exact revision reviews."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0042"
down_revision = "20261012_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_cell_revisions",
        sa.Column("author_type", sa.String(length=16), server_default="model", nullable=False),
    )
    op.add_column("analysis_cell_revisions", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column(
        "analysis_cell_revisions",
        sa.Column("reason", sa.String(length=2000), server_default="", nullable=False),
    )
    op.add_column(
        "analysis_cell_revisions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "analysis_cell_revisions",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column("analysis_cell_revisions", sa.Column("supersedes_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_analysis_cell_revision_scope",
        "analysis_cell_revisions",
        ["tenant_id", "unit_id", "id"],
    )
    op.create_unique_constraint(
        "uq_analysis_cell_revision_key",
        "analysis_cell_revisions",
        ["unit_id", "idempotency_key"],
    )
    op.create_check_constraint(
        op.f("ck_analysis_cell_revisions_author_type_valid"),
        "analysis_cell_revisions",
        "author_type IN ('model','user')",
    )
    op.create_check_constraint(
        op.f("ck_analysis_cell_revisions_idempotency_complete"),
        "analysis_cell_revisions",
        "(idempotency_key IS NULL) = (request_fingerprint IS NULL)",
    )
    op.create_foreign_key(
        op.f("fk_analysis_cell_revisions_tenant_id_unit_id_supersedes_id_analysis_cell_revisions"),
        "analysis_cell_revisions",
        "analysis_cell_revisions",
        ["tenant_id", "unit_id", "supersedes_id"],
        ["tenant_id", "unit_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "analysis_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("cell_revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
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
            "decision IN ('approved','changes_requested','rejected')",
            name=op.f("ck_analysis_reviews_decision_valid"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_analysis_reviews_revision_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id", "cell_revision_id"],
            [
                "analysis_cell_revisions.tenant_id",
                "analysis_cell_revisions.unit_id",
                "analysis_cell_revisions.id",
            ],
            name=op.f(
                "fk_analysis_reviews_tenant_id_unit_id_cell_revision_id_analysis_cell_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_reviews_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_reviews")),
        sa.UniqueConstraint("cell_revision_id", name="uq_analysis_review_revision"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_review_scope"),
        sa.UniqueConstraint("unit_id", "idempotency_key", name="uq_analysis_review_key"),
    )
    op.create_index(
        op.f("ix_analysis_reviews_unit_id"), "analysis_reviews", ["unit_id"], unique=False
    )
    op.create_table(
        "analysis_artifacts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("matrix_revision", sa.Integer(), nullable=False),
        sa.Column("matrix_hash", sa.String(length=64), nullable=False),
        sa.Column("matrix_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("cell_revision_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("object_bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint("format = 'xlsx'", name=op.f("ck_analysis_artifacts_format_valid")),
        sa.CheckConstraint(
            "matrix_revision > 0", name=op.f("ck_analysis_artifacts_matrix_revision_positive")
        ),
        sa.CheckConstraint(
            "(content_hash IS NULL AND size_bytes IS NULL) OR "
            "(content_hash IS NOT NULL AND size_bytes > 0)",
            name=op.f("ck_analysis_artifacts_content_metadata_pair"),
        ),
        sa.CheckConstraint(
            "mode IN ('draft','reviewed')", name=op.f("ck_analysis_artifacts_mode_valid")
        ),
        sa.CheckConstraint(
            "status IN ('writing','ready','approved','failed')",
            name=op.f("ck_analysis_artifacts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            name=op.f("fk_analysis_artifacts_tenant_id_run_id_analysis_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_artifacts")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_artifact_scope"),
        sa.UniqueConstraint("run_id", "matrix_revision", name="uq_analysis_artifact_revision"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_analysis_artifact_key"),
        sa.UniqueConstraint(
            "tenant_id", "object_bucket", "object_key", name="uq_analysis_artifact_location"
        ),
    )
    op.create_index(
        op.f("ix_analysis_artifacts_run_id"), "analysis_artifacts", ["run_id"], unique=False
    )
    op.create_table(
        "analysis_approvals",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("matrix_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
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
            "decision IN ('approved','rejected')",
            name=op.f("ck_analysis_approvals_decision_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > decided_at",
            name=op.f("ck_analysis_approvals_expiry_after_decision"),
        ),
        sa.CheckConstraint(
            "operation = 'deliver'", name=op.f("ck_analysis_approvals_operation_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["analysis_artifacts.tenant_id", "analysis_artifacts.id"],
            name=op.f("fk_analysis_approvals_tenant_id_artifact_id_analysis_artifacts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_approvals")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_approval_scope"),
        sa.UniqueConstraint("artifact_id", name="uq_analysis_approval_artifact"),
        sa.UniqueConstraint("artifact_id", "idempotency_key", name="uq_analysis_approval_key"),
    )
    op.create_index(
        op.f("ix_analysis_approvals_artifact_id"),
        "analysis_approvals",
        ["artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE analysis_cell_revisions, analysis_reviews, "
            "analysis_artifacts, analysis_approvals IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_reviews) OR "
            "EXISTS (SELECT 1 FROM analysis_artifacts) OR "
            "EXISTS (SELECT 1 FROM analysis_approvals)"
        )
    ):
        raise RuntimeError("analysis_review_history_present")
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_cell_revisions "
            "WHERE author_type != 'model' OR actor_id IS NOT NULL OR reason != '' "
            "OR idempotency_key IS NOT NULL OR request_fingerprint IS NOT NULL "
            "OR supersedes_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("analysis_revision_history_present")
    op.drop_index(op.f("ix_analysis_approvals_artifact_id"), table_name="analysis_approvals")
    op.drop_table("analysis_approvals")
    op.drop_index(op.f("ix_analysis_artifacts_run_id"), table_name="analysis_artifacts")
    op.drop_table("analysis_artifacts")
    op.drop_index(op.f("ix_analysis_reviews_unit_id"), table_name="analysis_reviews")
    op.drop_table("analysis_reviews")
    op.drop_constraint(
        op.f("fk_analysis_cell_revisions_tenant_id_unit_id_supersedes_id_analysis_cell_revisions"),
        "analysis_cell_revisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_analysis_cell_revisions_idempotency_complete"),
        "analysis_cell_revisions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_analysis_cell_revisions_author_type_valid"),
        "analysis_cell_revisions",
        type_="check",
    )
    op.drop_constraint("uq_analysis_cell_revision_key", "analysis_cell_revisions", type_="unique")
    op.drop_constraint("uq_analysis_cell_revision_scope", "analysis_cell_revisions", type_="unique")
    for column in (
        "supersedes_id",
        "request_fingerprint",
        "idempotency_key",
        "reason",
        "actor_id",
        "author_type",
    ):
        op.drop_column("analysis_cell_revisions", column)
