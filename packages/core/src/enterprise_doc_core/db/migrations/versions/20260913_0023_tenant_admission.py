"""Add one-time tenant admission, initial entitlements and operator audit."""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0023"
down_revision = "20260912_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    empty_receipt = " AND ".join(
        f"{column} IS NULL"
        for column in (
            "accepted_at",
            "accepted_tenant_id",
            "accepted_user_id",
            "accepted_membership_id",
            "accepted_binding_id",
            "accepted_entitlement_id",
            "accepted_identity_digest",
            "accepted_request_digest",
        )
    )
    full_receipt = empty_receipt.replace("IS NULL", "IS NOT NULL")
    op.create_table(
        "tenant_admission_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_tenant_id", sa.Uuid(), unique=True),
        sa.Column("accepted_user_id", sa.Uuid()),
        sa.Column("accepted_membership_id", sa.Uuid()),
        sa.Column("accepted_binding_id", sa.Uuid()),
        sa.Column("accepted_entitlement_id", sa.Uuid()),
        sa.Column("accepted_identity_digest", sa.String(64)),
        sa.Column("accepted_request_digest", sa.String(64)),
        sa.CheckConstraint("quota_bytes > 0 AND seat_limit > 0", name="positive_entitlements"),
        sa.CheckConstraint("token_digest ~ '^[0-9a-f]{64}$'", name="token_digest_valid"),
        sa.CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        sa.CheckConstraint(
            f"(state = 'pending' AND revoked_at IS NULL AND {empty_receipt}) OR "
            f"(state = 'revoked' AND revoked_at IS NOT NULL AND {empty_receipt}) OR "
            f"(state = 'accepted' AND revoked_at IS NULL AND {full_receipt})",
            name="state_receipt_consistent",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= issued_at AND accepted_at < expires_at)",
            name="acceptance_within_validity",
        ),
    )
    op.create_table(
        "tenant_initial_entitlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant_admission_grants.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quota_bytes > 0 AND seat_limit > 0", name="positive_entitlements"),
    )
    op.create_table(
        "tenant_admission_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant_admission_grants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operator_id", sa.String(128)),
        sa.Column("reason", sa.String(500)),
        sa.Column("accepted_user_id", sa.Uuid()),
        sa.CheckConstraint(
            "(action = 'accepted' AND accepted_user_id IS NOT NULL "
            "AND operator_id IS NULL AND reason IS NULL) OR "
            "(action IN ('issued', 'revoked') AND accepted_user_id IS NULL "
            "AND operator_id IS NOT NULL AND reason IS NOT NULL)",
            name="actor_matches_action",
        ),
    )
    op.create_index(
        "ix_tenant_admission_events_grant_id_occurred_at",
        "tenant_admission_events",
        ["grant_id", "occurred_at"],
    )
    op.create_index(
        "uq_tenant_admission_events_grant_id_action",
        "tenant_admission_events",
        ["grant_id", "action"],
        unique=True,
    )
    op.create_index("ix_users_normalized_email", "users", [sa.text("lower(email)")])
    op.create_index(
        "ix_external_identity_bindings_issuer_subject_user_id",
        "external_identity_bindings",
        ["issuer", "subject", "user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_identity_bindings_issuer_subject_user_id",
        table_name="external_identity_bindings",
    )
    op.drop_index("ix_users_normalized_email", table_name="users")
    op.drop_table("tenant_admission_events")
    op.drop_table("tenant_initial_entitlements")
    op.drop_table("tenant_admission_grants")
