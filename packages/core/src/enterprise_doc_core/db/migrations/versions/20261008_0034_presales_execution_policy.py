"""Preserve the user intent and bounded strategy of an accepted generation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261008_0034"
down_revision = "20261008_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presales_attempts", sa.Column("execution_policy", JSONB(), nullable=True))
    op.create_check_constraint(
        "presales_attempt_execution_policy_object",
        "presales_attempts",
        "execution_policy IS NULL OR jsonb_typeof(execution_policy) = 'object'",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE presales_attempts IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM presales_attempts WHERE execution_policy IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("presales_execution_policy_history_present")
    op.drop_constraint(
        "presales_attempt_execution_policy_object", "presales_attempts", type_="check"
    )
    op.drop_column("presales_attempts", "execution_policy")
