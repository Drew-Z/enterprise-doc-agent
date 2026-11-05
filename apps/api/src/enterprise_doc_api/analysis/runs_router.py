from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field
from starlette.responses import StreamingResponse

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.analysis.execution import AnalysisExecutionService
from enterprise_doc_core.analysis.execution_schemas import InputSubmission, MatrixView, RunView
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.context import PrincipalContext

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
Principal = Annotated[PrincipalContext, Depends(get_current_principal)]


class RunRequest(ApiModel):
    snapshot_id: UUID
    execution_mode: Literal["fixed", "agent"] = "fixed"
    base_run_id: UUID | None = None


class InputsRequest(ApiModel):
    submissions: tuple[InputSubmission, ...] = Field(min_length=1, max_length=30)


def _service(request: Request, *, start: bool = False) -> AnalysisExecutionService:
    if start and not request.app.state.analysis_execution_enabled:
        raise ApiError(
            status_code=409,
            code="analysis_execution_disabled",
            message="Analysis execution is not enabled.",
        )
    service = request.app.state.analysis_execution_service
    if service is None:
        raise ApiError(
            status_code=503, code="analysis_unavailable", message="Analysis is unavailable."
        )
    return cast(AnalysisExecutionService, service)


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except (AnalysisError, UsageError) as error:
        code = error.code
        status = (
            404
            if code in {"analysis_not_found", "analysis_input_not_found"}
            else 422
            if code == "analysis_input_invalid"
            else 503
            if "unavailable" in code
            else 429
            if "limit" in code or "budget_exhausted" in code
            else 409
        )
        raise ApiError(
            status_code=status, code=code, message="The analysis request could not be completed."
        ) from error


@router.post("/projects/{project_id}/runs", response_model=RunView, status_code=202)
async def create_run(
    project_id: UUID,
    body: RunRequest,
    request: Request,
    principal: Principal,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> RunView:
    with _errors():
        return await _service(request, start=True).create_run(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
            snapshot_id=body.snapshot_id,
            idempotency_key=idempotency_key,
            execution_mode=body.execution_mode,
            base_run_id=body.base_run_id,
        )


@router.get("/projects/{project_id}/runs", response_model=tuple[RunView, ...])
async def list_runs(
    project_id: UUID, request: Request, principal: Principal
) -> tuple[RunView, ...]:
    with _errors():
        return await _service(request).list_runs(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
        )


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: UUID, request: Request, principal: Principal) -> RunView:
    with _errors():
        return (
            await _service(request).matrix(
                tenant_id=UUID(principal.tenant_id),
                actor_id=UUID(principal.actor_id),
                run_id=run_id,
            )
        ).run


@router.get("/runs/{run_id}/matrix", response_model=MatrixView)
async def matrix(run_id: UUID, request: Request, principal: Principal) -> MatrixView:
    with _errors():
        return await _service(request).matrix(
            tenant_id=UUID(principal.tenant_id), actor_id=UUID(principal.actor_id), run_id=run_id
        )


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel(run_id: UUID, request: Request, principal: Principal) -> RunView:
    with _errors():
        return await _service(request).cancel(
            tenant_id=UUID(principal.tenant_id), actor_id=UUID(principal.actor_id), run_id=run_id
        )


@router.post("/runs/{run_id}/inputs", response_model=RunView, status_code=202)
async def submit_inputs(
    run_id: UUID,
    body: InputsRequest,
    request: Request,
    principal: Principal,
) -> RunView:
    with _errors():
        return await _service(request).submit_inputs(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            run_id=run_id,
            submissions=body.submissions,
        )


@router.get("/runs/{run_id}/events")
async def events(
    run_id: UUID,
    request: Request,
    principal: Principal,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
) -> StreamingResponse:
    if last_event_id is not None and (not last_event_id.isdecimal() or len(last_event_id) > 9):
        raise ApiError(
            status_code=422, code="analysis_event_cursor_invalid", message="Invalid event cursor."
        )
    with _errors():
        values = await _service(request).events(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            run_id=run_id,
            after=int(last_event_id or "0"),
        )

    # Finite authenticated batches. EventSource reconnects with Last-Event-ID;
    # matrix GET remains the authoritative projection after missed events.
    async def stream() -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        for value in values:
            yield (
                f"id: {value.sequence}\nevent: {value.event_type}\n"
                f"data: {value.model_dump_json(by_alias=True)}\n\n"
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
