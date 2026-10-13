"""Persist upload transport and direct PUT capability expiry; legacy rows stay multipart."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_0032"
down_revision = "20260924_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column("transport", sa.String(16), nullable=False, server_default="multipart"),
    )
    op.add_column("upload_sessions", sa.Column("signed_put_expires_at", sa.DateTime(timezone=True)))
    op.add_column("upload_sessions", sa.Column("single_put_retired_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "retired_put_transport",
        "upload_sessions",
        "transport = 'single_put' OR single_put_retired_at IS NULL",
    )
    op.create_check_constraint(
        "transport_valid", "upload_sessions", "transport IN ('multipart', 'single_put')"
    )
    op.create_check_constraint(
        "signed_put_transport",
        "upload_sessions",
        "transport = 'single_put' OR signed_put_expires_at IS NULL",
    )
    op.create_check_constraint(
        "single_put_no_multipart_id",
        "upload_sessions",
        "transport = 'multipart' OR object_store_upload_id IS NULL",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE upload_sessions IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM upload_sessions WHERE transport <> 'multipart')")
    ).scalar():
        raise RuntimeError("single_put_upload_history_present")
    op.drop_constraint("single_put_no_multipart_id", "upload_sessions", type_="check")
    op.drop_constraint("retired_put_transport", "upload_sessions", type_="check")
    op.drop_constraint("signed_put_transport", "upload_sessions", type_="check")
    op.drop_constraint("transport_valid", "upload_sessions", type_="check")
    op.drop_column("upload_sessions", "signed_put_expires_at")
    op.drop_column("upload_sessions", "single_put_retired_at")
    op.drop_column("upload_sessions", "transport")
