"""Add one-use OIDC attempts and revocable browser identity sessions."""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0024"
down_revision = "20260913_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    no_selection = " AND ".join(
        f"{name} IS NULL" for name in ("tenant_id", "user_id", "membership_id", "binding_id")
    )
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("credential_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("tenant_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("membership_id", sa.Uuid()),
        sa.Column("binding_id", sa.Uuid()),
        sa.CheckConstraint("credential_digest ~ '^[0-9a-f]{64}$'", name="credential_digest_valid"),
        sa.CheckConstraint("generation > 0", name="generation_positive"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint(
            f"({no_selection}) OR ({no_selection.replace('IS NULL', 'IS NOT NULL')})",
            name="selection_complete",
        ),
    )
    op.create_index("ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"])
    op.create_table(
        "browser_login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("verifier_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("nonce_digest", sa.String(64), nullable=False),
        sa.Column("client_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("completed_session_id", sa.Uuid()),
        sa.Column("completed_generation", sa.BigInteger()),
        sa.Column("previous_session_id", sa.Uuid()),
        sa.Column("previous_generation", sa.BigInteger()),
        sa.Column("previous_credential_digest", sa.String(64)),
        sa.CheckConstraint(
            "state_digest ~ '^[0-9a-f]{64}$' AND verifier_digest ~ '^[0-9a-f]{64}$' "
            "AND nonce_digest ~ '^[0-9a-f]{64}$' AND client_digest ~ '^[0-9a-f]{64}$'",
            name="digests_valid",
        ),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at < expires_at)",
            name="consumption_within_validity",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR (consumed_at IS NOT NULL "
            "AND completed_at >= consumed_at AND completed_at < expires_at)",
            name="completion_after_consumption",
        ),
        sa.CheckConstraint(
            "(previous_session_id IS NULL AND previous_generation IS NULL) OR "
            "(previous_session_id IS NOT NULL AND previous_generation > 0 "
            "AND previous_credential_digest IS NOT NULL)",
            name="previous_context_complete",
        ),
        sa.CheckConstraint(
            "(completed_at IS NULL AND completed_session_id IS NULL "
            "AND completed_generation IS NULL) OR (completed_at IS NOT NULL "
            "AND completed_session_id IS NOT NULL AND completed_generation > 0)",
            name="completed_context_complete",
        ),
    )
    op.create_index(
        "ix_browser_login_attempts_client_digest_created_at",
        "browser_login_attempts",
        ["client_digest", "created_at"],
    )
    op.create_table(
        "browser_session_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("browser_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
        sa.CheckConstraint(
            "action IN ('signed_in', 'selected', 'signed_out')", name="action_valid"
        ),
        sa.CheckConstraint("generation > 0", name="generation_positive"),
        sa.UniqueConstraint("session_id", "generation"),
    )


def downgrade() -> None:
    op.drop_table("browser_session_events")
    op.drop_table("browser_login_attempts")
    op.drop_table("browser_sessions")
