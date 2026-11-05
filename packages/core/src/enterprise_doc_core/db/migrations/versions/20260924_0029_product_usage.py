"""Add separate finite Agent task and document processing quotas."""

import sqlalchemy as sa
from alembic import op

revision = "20260924_0029"
down_revision = "20260924_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_jobs_tenant_id_id", "jobs", ["tenant_id", "id"])
    op.add_column(
        "document_ingestion_generations", sa.Column("processing_job_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ingestion_generation_processing_job",
        "document_ingestion_generations",
        "jobs",
        ["tenant_id", "processing_job_id"],
        ["tenant_id", "id"],
    )
    op.create_index(
        "ix_ingestion_generation_processing_job",
        "document_ingestion_generations",
        ["tenant_id", "processing_job_id"],
    )
    op.create_table(
        "product_quotas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("unit_limit", sa.BigInteger(), nullable=False),
        sa.Column("units_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("units_reserved", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "id", "metric", name="uq_product_quotas_tenant_metric_id"),
        sa.UniqueConstraint(
            "tenant_id", "entitlement_id", "metric", name="uq_product_quotas_period_metric"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("metric IN ('agent_task', 'document_bytes')", name="metric_valid"),
        sa.CheckConstraint(
            "unit_limit >= 0 AND units_used >= 0 AND units_reserved >= 0", name="units_non_negative"
        ),
        sa.CheckConstraint("units_used <= unit_limit - units_reserved", name="within_limit"),
    )
    op.create_table(
        "product_usage_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quota_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_product_usage_reservations_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "metric", "operation_id", name="uq_product_usage_operation"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quota_id", "metric"],
            ["product_quotas.tenant_id", "product_quotas.id", "product_quotas.metric"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("metric != 'agent_task' OR quantity = 1", name="one_task"),
        sa.CheckConstraint("state IN ('reserved', 'consumed', 'released')", name="state_valid"),
    )
    op.create_index(
        "ix_product_usage_quota_state",
        "product_usage_reservations",
        ["tenant_id", "quota_id", "state"],
    )

    op.create_table(
        "product_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "reservation_id", name="uq_product_usage_terminal_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reservation_id"],
            ["product_usage_reservations.tenant_id", "product_usage_reservations.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("event_type IN ('consume', 'release')", name="event_type_valid"),
    )
    op.create_index(
        "ix_product_usage_events_tenant_time", "product_usage_events", ["tenant_id", "occurred_at"]
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM product_quotas) OR EXISTS "
                "(SELECT 1 FROM document_ingestion_generations WHERE processing_job_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("product_usage_history_present")
    op.drop_table("product_usage_events")
    op.drop_table("product_usage_reservations")
    op.drop_table("product_quotas")
    op.drop_index("ix_ingestion_generation_processing_job", "document_ingestion_generations")
    op.drop_constraint(
        "fk_ingestion_generation_processing_job",
        "document_ingestion_generations",
        type_="foreignkey",
    )
    op.drop_column("document_ingestion_generations", "processing_job_id")
    op.drop_constraint("uq_jobs_tenant_id_id", "jobs", type_="unique")
