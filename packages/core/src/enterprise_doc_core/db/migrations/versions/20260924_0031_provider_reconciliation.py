"""Retain supplier identifiers for private usage reconciliation."""

import sqlalchemy as sa
from alembic import op

revision = "20260924_0031"
down_revision = "20260924_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_dispatches", sa.Column("provider_request_id", sa.String(200)))
    op.add_column("provider_dispatches", sa.Column("provider_response_id", sa.String(200)))
    op.add_column("presales_provider_calls", sa.Column("provider_request_id", sa.String(200)))
    op.create_index(
        "ix_presales_provider_calls_tenant_time",
        "presales_provider_calls",
        ["tenant_id", "started_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE provider_dispatches, presales_provider_calls IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM provider_dispatches "
            "WHERE provider_request_id IS NOT NULL OR provider_response_id IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM presales_provider_calls "
            "WHERE provider_request_id IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("provider_reconciliation_history_present")
    op.drop_index("ix_presales_provider_calls_tenant_time", table_name="presales_provider_calls")
    op.drop_column("presales_provider_calls", "provider_request_id")
    op.drop_column("provider_dispatches", "provider_response_id")
    op.drop_column("provider_dispatches", "provider_request_id")
