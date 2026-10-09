"""Keep each human review's evidence separate from the original draft."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261010_0037"
down_revision = "20261009_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presales_reviews", sa.Column("citations", JSONB(), nullable=True))
    op.create_check_constraint(
        "presales_review_citations_array",
        "presales_reviews",
        "citations IS NULL OR CASE WHEN jsonb_typeof(citations) = 'array' "
        "THEN jsonb_array_length(citations) <= 12 ELSE false END",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE presales_reviews IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM presales_reviews WHERE citations IS NOT NULL)")
    ).scalar():
        raise RuntimeError("presales_review_citations_history_present")
    op.drop_constraint("presales_review_citations_array", "presales_reviews", type_="check")
    op.drop_column("presales_reviews", "citations")
