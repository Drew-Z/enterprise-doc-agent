"""Retain the original questionnaire and confirmed cell mapping with its packet."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261009_0035"
down_revision = "20261008_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presales_packets", sa.Column("workbook_metadata", JSONB(), nullable=True))
    op.add_column(
        "presales_packets", sa.Column("workbook_content", sa.LargeBinary(), nullable=True)
    )
    op.create_check_constraint(
        "presales_workbook_valid",
        "presales_packets",
        "(workbook_metadata IS NULL AND workbook_content IS NULL) OR "
        "(workbook_metadata IS NOT NULL AND jsonb_typeof(workbook_metadata) = 'object' "
        "AND workbook_content IS NOT NULL "
        "AND octet_length(workbook_content) BETWEEN 1 AND 2097152)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE presales_packets IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM presales_packets "
            "WHERE workbook_metadata IS NOT NULL OR workbook_content IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("presales_workbook_history_present")
    op.drop_constraint("presales_workbook_valid", "presales_packets", type_="check")
    op.drop_column("presales_packets", "workbook_content")
    op.drop_column("presales_packets", "workbook_metadata")
