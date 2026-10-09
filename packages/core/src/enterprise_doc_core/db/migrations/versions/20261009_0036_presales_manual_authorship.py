"""Keep human authorship outside the historical strict draft JSON contract."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261009_0036"
down_revision = "20261009_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presales_rows", sa.Column("manual_authorship", JSONB(), nullable=True))
    op.create_check_constraint(
        "presales_manual_authorship_valid",
        "presales_rows",
        "manual_authorship IS NULL OR (jsonb_typeof(manual_authorship) = 'object' "
        "AND draft IS NOT NULL AND jsonb_typeof(draft) = 'object' AND revision >= 1)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE presales_rows IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM presales_rows WHERE manual_authorship IS NOT NULL)")
    ).scalar():
        raise RuntimeError("presales_manual_history_present")
    op.drop_constraint("presales_manual_authorship_valid", "presales_rows", type_="check")
    op.drop_column("presales_rows", "manual_authorship")
