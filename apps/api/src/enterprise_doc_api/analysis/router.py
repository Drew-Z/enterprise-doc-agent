from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import Field

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.analysis.execution_schemas import ReviewView
from enterprise_doc_core.analysis.review import AnalysisReviewDeliveryService
from enterprise_doc_core.analysis.review_schemas import (
    ArtifactApprovalInput,
    ArtifactApprovalView,
    ArtifactDownloadView,
    ArtifactView,
    CellRevisionView,
    ExportInput,
    ManualRevisionInput,
    ReviewInput,
)
from enterprise_doc_core.analysis.schemas import (
    AnalysisField,
    AnalysisSpec,
    ProjectView,
    SnapshotView,
    SourceBlocksView,
    default_fields,
)
from enterprise_doc_core.analysis.service import AnalysisError, AnalysisWorkspaceService
from enterprise_doc_core.context import PrincipalContext

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
Principal = Annotated[PrincipalContext, Depends(get_current_principal)]


class CapabilitiesResponse(ApiModel):
    admission_enabled: bool
    execution_enabled: bool
    structured_uploads_enabled: bool
    max_source_files: int = 10
    max_objects: int = 3
    max_fields: int = 10
    max_file_bytes: int = 10 * 1024**2
    template: tuple[AnalysisField, ...]


class ProjectRequest(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    spec: AnalysisSpec


def _service(request: Request, *, write: bool = False) -> AnalysisWorkspaceService:
    if write and not request.app.state.analysis_admission_enabled:
        raise ApiError(
            status_code=409,
            code="analysis_disabled",
            message="New analysis projects are not enabled.",
        )
    value = request.app.state.analysis_service
    if value is None:
        raise ApiError(
            status_code=503,
            code="analysis_unavailable",
            message="Analysis workspace is unavailable.",
        )
    return cast(AnalysisWorkspaceService, value)


def _review_service(request: Request) -> AnalysisReviewDeliveryService:
    service = request.app.state.analysis_execution_service
    if not isinstance(service, AnalysisReviewDeliveryService):
        raise ApiError(
            status_code=503,
            code="analysis_review_unavailable",
            message="Analysis review is unavailable.",
        )
    return service


def _revision(if_match: str | None, *, allow_zero: bool = False) -> int:
    if if_match is None:
        raise ApiError(
            status_code=428,
            code="analysis_revision_required",
            message="The current project revision is required.",
        )
    if (
        len(if_match) < 3
        or not if_match.startswith('"')
        or not if_match.endswith('"')
        or not if_match[1:-1].isdigit()
    ):
        raise ApiError(
            status_code=422,
            code="analysis_revision_invalid",
            message="The project revision is invalid.",
        )
    value = int(if_match[1:-1])
    if value < (0 if allow_zero else 1):
        raise ApiError(
            status_code=422,
            code="analysis_revision_invalid",
            message="The project revision is invalid.",
        )
    return value


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except AnalysisError as error:
        status_code = 404 if error.code == "analysis_not_found" else 409
        raise ApiError(
            status_code=status_code,
            code=error.code,
            message="The analysis request could not be completed.",
        ) from error


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(request: Request, principal: Principal) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        admission_enabled=request.app.state.analysis_admission_enabled,
        execution_enabled=request.app.state.analysis_execution_enabled,
        structured_uploads_enabled=request.app.state.analysis_admission_enabled,
        template=default_fields(),
    )


@router.get("/projects", response_model=tuple[ProjectView, ...])
async def list_projects(request: Request, principal: Principal) -> tuple[ProjectView, ...]:
    with _errors():
        return await _service(request).list_projects(
            tenant_id=UUID(principal.tenant_id), actor_id=UUID(principal.actor_id)
        )


@router.post("/projects", response_model=ProjectView, status_code=201)
async def create_project(
    body: ProjectRequest, request: Request, response: Response, principal: Principal
) -> ProjectView:
    with _errors():
        view = await _service(request, write=True).create_project(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            title=body.title,
            spec=body.spec,
        )
    response.headers["ETag"] = f'"{view.revision}"'
    return view


@router.get("/projects/{project_id}", response_model=ProjectView)
async def get_project(
    project_id: UUID, request: Request, response: Response, principal: Principal
) -> ProjectView:
    with _errors():
        view = await _service(request).get_project(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
        )
    response.headers["ETag"] = f'"{view.revision}"'
    return view


@router.patch("/projects/{project_id}", response_model=ProjectView)
async def update_project(
    project_id: UUID,
    body: ProjectRequest,
    request: Request,
    response: Response,
    principal: Principal,
    if_match: Annotated[str | None, Header()] = None,
) -> ProjectView:
    revision = _revision(if_match)
    with _errors():
        view = await _service(request, write=True).update_project(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
            expected_revision=revision,
            title=body.title,
            spec=body.spec,
        )
    response.headers["ETag"] = f'"{view.revision}"'
    return view


@router.post("/projects/{project_id}/snapshots", response_model=SnapshotView)
async def freeze_sources(
    project_id: UUID,
    request: Request,
    principal: Principal,
    if_match: Annotated[str | None, Header()] = None,
) -> SnapshotView:
    revision = _revision(if_match)
    with _errors():
        return await _service(request, write=True).freeze_sources(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
            expected_revision=revision,
        )


@router.get("/projects/{project_id}/snapshots", response_model=tuple[SnapshotView, ...])
async def list_snapshots(
    project_id: UUID, request: Request, principal: Principal
) -> tuple[SnapshotView, ...]:
    with _errors():
        return await _service(request).list_snapshots(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            project_id=project_id,
        )


@router.get("/snapshots/{snapshot_id}/sources/{version_id}/blocks", response_model=SourceBlocksView)
async def source_blocks(
    snapshot_id: UUID,
    version_id: UUID,
    request: Request,
    principal: Principal,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SourceBlocksView:
    with _errors():
        return await _service(request).source_blocks(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            snapshot_id=snapshot_id,
            document_version_id=version_id,
            offset=offset,
            limit=limit,
        )


@router.post("/cells/{unit_id}/revisions", response_model=CellRevisionView, status_code=201)
async def create_cell_revision(
    unit_id: UUID,
    body: ManualRevisionInput,
    request: Request,
    principal: Principal,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    if_match: Annotated[str | None, Header()] = None,
) -> CellRevisionView:
    revision = _revision(if_match, allow_zero=True)
    with _errors():
        return await _review_service(request).create_cell_revision(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            unit_id=unit_id,
            expected_revision=revision,
            idempotency_key=idempotency_key,
            request=body,
        )


@router.get("/cells/{unit_id}/revisions", response_model=tuple[CellRevisionView, ...])
async def list_cell_revisions(
    unit_id: UUID, request: Request, principal: Principal
) -> tuple[CellRevisionView, ...]:
    with _errors():
        return await _review_service(request).cell_revisions(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            unit_id=unit_id,
        )


@router.post("/cells/{unit_id}/reviews", response_model=ReviewView, status_code=201)
async def review_cell_revision(
    unit_id: UUID,
    body: ReviewInput,
    request: Request,
    principal: Principal,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> ReviewView:
    with _errors():
        return await _review_service(request).review_cell_revision(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            unit_id=unit_id,
            idempotency_key=idempotency_key,
            request=body,
        )


@router.post("/runs/{run_id}/exports", response_model=ArtifactView, status_code=201)
async def create_export(
    run_id: UUID,
    body: ExportInput,
    request: Request,
    principal: Principal,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> ArtifactView:
    with _errors():
        return await _review_service(request).create_export(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            run_id=run_id,
            idempotency_key=idempotency_key,
            request=body,
        )


@router.get("/runs/{run_id}/artifacts", response_model=tuple[ArtifactView, ...])
async def list_artifacts(
    run_id: UUID, request: Request, principal: Principal
) -> tuple[ArtifactView, ...]:
    with _errors():
        return await _review_service(request).list_artifacts(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            run_id=run_id,
        )


@router.post(
    "/artifacts/{artifact_id}/approvals",
    response_model=ArtifactApprovalView,
    status_code=201,
)
async def approve_artifact(
    artifact_id: UUID,
    body: ArtifactApprovalInput,
    request: Request,
    principal: Principal,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> ArtifactApprovalView:
    with _errors():
        return await _review_service(request).approve_artifact(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            artifact_id=artifact_id,
            idempotency_key=idempotency_key,
            request=body,
        )


@router.get("/artifacts/{artifact_id}/download", response_model=ArtifactDownloadView)
async def download_artifact(
    artifact_id: UUID, request: Request, principal: Principal
) -> ArtifactDownloadView:
    with _errors():
        return await _review_service(request).download_artifact(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            artifact_id=artifact_id,
        )
