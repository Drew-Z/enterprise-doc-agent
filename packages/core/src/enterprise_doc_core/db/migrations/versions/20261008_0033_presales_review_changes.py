"""Keep human prerequisite origins separately from backward-readable review content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261008_0033"
down_revision = "20261005_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presales_reviews", sa.Column("prerequisite_changes", JSONB(), nullable=True))
    op.create_check_constraint(
        "presales_review_changes_object",
        "presales_reviews",
        "prerequisite_changes IS NULL OR jsonb_typeof(prerequisite_changes) = 'object'",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE presales_reviews IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM presales_reviews WHERE prerequisite_changes IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("presales_review_changes_history_present")
    op.drop_constraint("presales_review_changes_object", "presales_reviews", type_="check")
    op.drop_column("presales_reviews", "prerequisite_changes")
