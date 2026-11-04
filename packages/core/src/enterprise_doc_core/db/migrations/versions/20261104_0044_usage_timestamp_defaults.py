"""Align usage timestamps with ORM defaults without rewriting historical rows."""

import sqlalchemy as sa
from alembic import op

revision = "20261104_0044"
down_revision = "20261012_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tenant_entitlements", "usage_reservations"):
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table,
                column,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
                server_default=sa.func.now(),
            )


def downgrade() -> None:
    for table in ("tenant_entitlements", "usage_reservations"):
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table,
                column,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
                server_default=None,
            )
