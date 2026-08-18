"""Persist per-run provider identity and usage telemetry.

Revision ID: 20260817_0013
Revises: 20260811_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0013"
down_revision = "20260811_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("model_revision", sa.String(length=128), nullable=True))
    op.add_column("agent_runs", sa.Column("provider_request_count", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("provider_usage_request_count", sa.Integer(), nullable=True)
    )
    op.add_column("agent_runs", sa.Column("prompt_tokens", sa.BigInteger(), nullable=True))
    op.add_column("agent_runs", sa.Column("completion_tokens", sa.BigInteger(), nullable=True))
    op.add_column("agent_runs", sa.Column("total_tokens", sa.BigInteger(), nullable=True))
    op.add_column("agent_runs", sa.Column("repair_request_count", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("fallback_count", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("breaker_state", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_agent_runs_provider_telemetry_counts_non_negative",
        "agent_runs",
        "(provider_request_count IS NULL OR provider_request_count >= 0) AND "
        "(provider_usage_request_count IS NULL OR provider_usage_request_count >= 0) AND "
        "(repair_request_count IS NULL OR repair_request_count >= 0) AND "
        "(fallback_count IS NULL OR fallback_count >= 0)",
    )
    op.create_check_constraint(
        "ck_agent_runs_provider_usage_count_bounded",
        "agent_runs",
        "provider_request_count IS NULL OR provider_usage_request_count IS NULL OR "
        "provider_usage_request_count <= provider_request_count",
    )
    op.create_check_constraint(
        "ck_agent_runs_repair_request_count_bounded",
        "agent_runs",
        "provider_request_count IS NULL OR repair_request_count IS NULL OR "
        "repair_request_count <= provider_request_count",
    )
    op.create_check_constraint(
        "ck_agent_runs_provider_token_counts_non_negative",
        "agent_runs",
        "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND "
        "(completion_tokens IS NULL OR completion_tokens >= 0) AND "
        "(total_tokens IS NULL OR total_tokens >= 0)",
    )
    op.create_check_constraint(
        "ck_agent_runs_breaker_state_valid",
        "agent_runs",
        "breaker_state IS NULL OR breaker_state IN ('closed', 'open', 'half_open')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_runs_breaker_state_valid", "agent_runs", type_="check")
    op.drop_constraint(
        "ck_agent_runs_provider_token_counts_non_negative", "agent_runs", type_="check"
    )
    op.drop_constraint("ck_agent_runs_repair_request_count_bounded", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_provider_usage_count_bounded", "agent_runs", type_="check")
    op.drop_constraint(
        "ck_agent_runs_provider_telemetry_counts_non_negative", "agent_runs", type_="check"
    )
    op.drop_column("agent_runs", "breaker_state")
    op.drop_column("agent_runs", "fallback_count")
    op.drop_column("agent_runs", "repair_request_count")
    op.drop_column("agent_runs", "total_tokens")
    op.drop_column("agent_runs", "completion_tokens")
    op.drop_column("agent_runs", "prompt_tokens")
    op.drop_column("agent_runs", "provider_usage_request_count")
    op.drop_column("agent_runs", "provider_request_count")
    op.drop_column("agent_runs", "model_revision")
