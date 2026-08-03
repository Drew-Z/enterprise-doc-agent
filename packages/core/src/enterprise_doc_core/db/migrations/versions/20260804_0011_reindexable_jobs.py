"""Allow a document version to have jobs for multiple ingestion generations.

Revision ID: 20260804_0011
Revises: 20260803_0010
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260804_0011"
down_revision = "20260803_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_CONSTRAINT = "uq_jobs_document_version_id"
LOOKUP_INDEX = "ix_jobs_document_version_id_created_at"


def upgrade() -> None:
    op.drop_constraint(UNIQUE_CONSTRAINT, "jobs", type_="unique")
    op.create_index(
        LOOKUP_INDEX,
        "jobs",
        ["document_version_id", "created_at"],
    )


def downgrade() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT document_version_id
            FROM jobs
            WHERE document_version_id IS NOT NULL
            GROUP BY document_version_id
            HAVING count(*) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "cannot restore one-job-per-document-version constraint while duplicate jobs exist"
        )
    op.drop_index(LOOKUP_INDEX, table_name="jobs")
    op.create_unique_constraint(UNIQUE_CONSTRAINT, "jobs", ["document_version_id"])
