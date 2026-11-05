from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.analysis.execution import ANALYSIS_JOB_TYPE, project_failure
from enterprise_doc_core.jobs.service import ClaimedJob
from enterprise_doc_worker.agent_handler import agent_failure_lock_key, project_agent_run_failure


def execution_failure_lock_key(claim: ClaimedJob) -> str | None:
    if claim.job_type != ANALYSIS_JOB_TYPE:
        return agent_failure_lock_key(claim)
    try:
        run_id = UUID(str(claim.payload["run_id"]))
    except (ValueError, KeyError, TypeError):
        return None
    return f"analysis-run:{run_id}"


async def project_execution_failure(
    session: AsyncSession,
    *,
    claim: ClaimedJob,
    status: str,
    error_code: str,
    error_message: str,
    failure_metadata: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    if claim.job_type == ANALYSIS_JOB_TYPE:
        if status in {"dead", "cancelled"}:
            await project_failure(
                session,
                claim,
                code="analysis_job_failed" if status == "dead" else "analysis_cancelled",
            )
    else:
        await project_agent_run_failure(
            session,
            claim=claim,
            status=status,
            error_code=error_code,
            error_message=error_message,
            failure_metadata=failure_metadata,
            now=now,
        )
