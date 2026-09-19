"""Add membership invitations and durable operation/acceptance history."""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0025"
down_revision = "20260913_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    receipt = (
        "accepted_at",
        "accepted_user_id",
        "accepted_membership_id",
        "accepted_binding_id",
        "accepted_identity_digest",
    )
    empty = " AND ".join(f"{name} IS NULL" for name in receipt)
    full = " AND ".join(f"{name} IS NOT NULL" for name in receipt)
    op.create_table(
        "membership_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("issued_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_user_id", sa.Uuid()),
        sa.Column("accepted_membership_id", sa.Uuid()),
        sa.Column("accepted_binding_id", sa.Uuid()),
        sa.Column("accepted_identity_digest", sa.String(64)),
        sa.CheckConstraint("generation > 0", name="positive_generation"),
        sa.CheckConstraint("token_digest ~ '^[0-9a-f]{64}$'", name="token_digest_valid"),
        sa.CheckConstraint(
            "expires_at > issued_at AND issued_at >= created_at AND updated_at >= issued_at",
            name="valid_times",
        ),
        sa.CheckConstraint(
            f"(state = 'pending' AND revoked_at IS NULL AND {empty}) OR "
            f"(state = 'revoked' AND revoked_at IS NOT NULL AND {empty}) OR "
            f"(state = 'accepted' AND revoked_at IS NULL AND {full})",
            name="state_receipt_consistent",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= issued_at AND accepted_at < expires_at)",
            name="acceptance_within_validity",
        ),
    )
    op.create_index(
        "uq_membership_invitations_pending_email",
        "membership_invitations",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_membership_invitations_tenant_created",
        "membership_invitations",
        ["tenant_id", "created_at"],
    )
    op.create_table(
        "membership_invitation_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "invitation_id",
            sa.Uuid(),
            sa.ForeignKey("membership_invitations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("request_digest", sa.String(64)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="positive_generation"),
        sa.CheckConstraint(
            "(action = 'accepted' AND operation_id IS NULL AND request_digest IS NULL) OR "
            "(action IN ('issued', 'regenerated', 'revoked') AND operation_id IS NOT NULL "
            "AND request_digest IS NOT NULL AND request_digest ~ '^[0-9a-f]{64}$')",
            name="valid_operation",
        ),
    )
    op.create_index(
        "uq_membership_invitation_operation",
        "membership_invitation_events",
        ["tenant_id", "operation_id"],
        unique=True,
    )
    op.create_index(
        "uq_membership_invitation_generation_action",
        "membership_invitation_events",
        ["invitation_id", "generation", "action"],
        unique=True,
    )
    op.create_index(
        "ix_membership_invitation_events_tenant_time",
        "membership_invitation_events",
        ["tenant_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("membership_invitation_events")
    op.drop_table("membership_invitations")
