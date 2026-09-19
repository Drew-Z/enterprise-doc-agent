"""presales_workspace

Revision ID: 20260912_0021
Revises: 20260827_0020
Create Date: 2026-09-12 05:59:21.767416
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0021"
down_revision: str | None = "20260827_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "presales_packets",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name=op.f("fk_presales_packets_tenant_id_actor_id_memberships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_presales_packets_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presales_packets")),
        sa.UniqueConstraint(
            "tenant_id", "actor_id", "idempotency_key", name="uq_presales_packets_create_key"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_presales_packets_tenant_id"),
    )
    op.create_index(
        "ix_presales_packets_actor_created",
        "presales_packets",
        ["tenant_id", "actor_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "presales_rows",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("packet_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("requirement_key", sa.String(length=40), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("source_location", sa.String(length=300), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 0", name=op.f("ck_presales_rows_presales_row_revision_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "packet_id"],
            ["presales_packets.tenant_id", "presales_packets.id"],
            name=op.f("fk_presales_rows_tenant_id_packet_id_presales_packets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presales_rows")),
        sa.UniqueConstraint("packet_id", "position", name="uq_presales_rows_position"),
        sa.UniqueConstraint("packet_id", "requirement_key", name="uq_presales_rows_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_presales_rows_tenant_id"),
    )
    op.create_table(
        "presales_attempts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("model_provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("provider_request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'expired')",
            name=op.f("ck_presales_attempts_presales_attempt_state_valid"),
        ),
        sa.CheckConstraint(
            "number BETWEEN 1 AND 3 AND provider_request_count BETWEEN 0 AND 1",
            name=op.f("ck_presales_attempts_presales_attempt_limits"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "row_id"],
            ["presales_rows.tenant_id", "presales_rows.id"],
            name=op.f("fk_presales_attempts_tenant_id_row_id_presales_rows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presales_attempts")),
        sa.UniqueConstraint("row_id", "idempotency_key", name="uq_presales_attempts_key"),
        sa.UniqueConstraint("row_id", "number", name="uq_presales_attempts_number"),
    )
    op.create_index(
        "ix_presales_attempts_tenant_started",
        "presales_attempts",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "presales_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_presales_reviews_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "row_id"],
            ["presales_rows.tenant_id", "presales_rows.id"],
            name=op.f("fk_presales_reviews_tenant_id_row_id_presales_rows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presales_reviews")),
        sa.UniqueConstraint("row_id", "idempotency_key", name="uq_presales_reviews_key"),
        sa.UniqueConstraint("row_id", "revision", name="uq_presales_reviews_revision"),
    )


def downgrade() -> None:
    op.drop_table("presales_reviews")
    op.drop_index("ix_presales_attempts_tenant_started", table_name="presales_attempts")
    op.drop_table("presales_attempts")
    op.drop_table("presales_rows")
    op.drop_index("ix_presales_packets_actor_created", table_name="presales_packets")
    op.drop_table("presales_packets")
