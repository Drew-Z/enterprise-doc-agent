"""Add conservative analysis dependency invalidation and reuse lineage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0043"
down_revision = "20261012_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("base_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_analysis_runs_tenant_id_base_run_id_analysis_runs"),
        "analysis_runs",
        "analysis_runs",
        ["tenant_id", "base_run_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "analysis_units",
        sa.Column(
            "input_fingerprint",
            sa.String(length=64),
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
            nullable=False,
        ),
    )
    op.add_column(
        "analysis_units",
        sa.Column("freshness", sa.String(length=16), server_default="current", nullable=False),
    )
    op.add_column(
        "analysis_units",
        sa.Column(
            "invalidation_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("analysis_units", sa.Column("reused_from_unit_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_analysis_units_freshness_valid"),
        "analysis_units",
        "freshness IN ('current','stale')",
    )
    op.create_check_constraint(
        op.f("ck_analysis_units_invalidation_reasons_array"),
        "analysis_units",
        "jsonb_typeof(invalidation_reasons) = 'array'",
    )
    op.create_foreign_key(
        op.f("fk_analysis_units_tenant_id_reused_from_unit_id_analysis_units"),
        "analysis_units",
        "analysis_units",
        ["tenant_id", "reused_from_unit_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_unique_constraint(
        "uq_analysis_cell_revision_tenant", "analysis_cell_revisions", ["tenant_id", "id"]
    )
    op.add_column(
        "analysis_cell_revisions",
        sa.Column("reused_from_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f(
            "fk_analysis_cell_revisions_tenant_id_reused_from_revision_id_analysis_cell_revisions"
        ),
        "analysis_cell_revisions",
        "analysis_cell_revisions",
        ["tenant_id", "reused_from_revision_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "analysis_dependencies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("dependency_key", sa.String(length=160), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('object','policy','field','parameter','object_source',"
            "'common_source','upstream_cell')",
            name=op.f("ck_analysis_dependencies_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_dependencies_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_dependencies")),
        sa.UniqueConstraint("unit_id", "kind", "dependency_key", name="uq_analysis_dependency_key"),
    )
    op.create_index(
        op.f("ix_analysis_dependencies_unit_id"),
        "analysis_dependencies",
        ["unit_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE analysis_runs, analysis_units, analysis_cell_revisions, "
            "analysis_dependencies IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_dependencies) OR "
            "EXISTS (SELECT 1 FROM analysis_runs WHERE base_run_id IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM analysis_units WHERE reused_from_unit_id IS NOT NULL "
            "OR freshness != 'current' OR invalidation_reasons != '[]'::jsonb) OR "
            "EXISTS (SELECT 1 FROM analysis_cell_revisions "
            "WHERE reused_from_revision_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("analysis_dependency_history_present")
    op.drop_index(op.f("ix_analysis_dependencies_unit_id"), table_name="analysis_dependencies")
    op.drop_table("analysis_dependencies")
    op.drop_constraint(
        op.f(
            "fk_analysis_cell_revisions_tenant_id_reused_from_revision_id_analysis_cell_revisions"
        ),
        "analysis_cell_revisions",
        type_="foreignkey",
    )
    op.drop_column("analysis_cell_revisions", "reused_from_revision_id")
    op.drop_constraint(
        "uq_analysis_cell_revision_tenant", "analysis_cell_revisions", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_analysis_units_tenant_id_reused_from_unit_id_analysis_units"),
        "analysis_units",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_analysis_units_invalidation_reasons_array"),
        "analysis_units",
        type_="check",
    )
    op.drop_constraint(op.f("ck_analysis_units_freshness_valid"), "analysis_units", type_="check")
    for column in (
        "reused_from_unit_id",
        "invalidation_reasons",
        "freshness",
        "input_fingerprint",
    ):
        op.drop_column("analysis_units", column)
    op.drop_constraint(
        op.f("fk_analysis_runs_tenant_id_base_run_id_analysis_runs"),
        "analysis_runs",
        type_="foreignkey",
    )
    op.drop_column("analysis_runs", "base_run_id")
