from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.agents import (
    AgentArtifactDownloadResult,
    AgentArtifactError,
    AgentArtifactIntegrityError,
    AgentArtifactNotFound,
    AgentArtifactPrincipalForbidden,
    AgentArtifactResult,
    AgentArtifactStoreUnavailable,
)
from enterprise_doc_core.context import PrincipalContext


class AgentArtifactServiceProtocol(Protocol):
    async def list_for_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentArtifactResult, ...]: ...

    async def get_download(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
    ) -> AgentArtifactDownloadResult: ...


class AgentArtifactResponse(ApiModel):
    artifact_id: UUID
    run_id: UUID
    document_version_id: UUID
    kind: str
    status: str
    content_type: str
    content_sha256: str
    size_bytes: int
    created_at: datetime
    verified_at: datetime
    published_at: datetime | None


class AgentArtifactDownloadResponse(ApiModel):
    artifact_id: UUID
    status: str
    content_type: str
    content_sha256: str
    size_bytes: int
    url: str
    expires_in_seconds: int


router = APIRouter(prefix="/api", tags=["agent-artifacts"])


@router.get(
    "/agent-runs/{run_id}/artifacts",
    response_model=list[AgentArtifactResponse],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_agent_artifacts(
    run_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[AgentArtifactResponse]:
    service = cast(AgentArtifactServiceProtocol, request.app.state.agent_artifact_service)
    try:
        results = await service.list_for_run(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            run_id=run_id,
        )
    except AgentArtifactError as error:
        raise _artifact_api_error(error) from error
    return [_artifact_response(result) for result in results]


@router.get(
    "/agent-artifacts/{artifact_id}/download",
    response_model=AgentArtifactDownloadResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_agent_artifact_download(
    artifact_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AgentArtifactDownloadResponse:
    service = cast(AgentArtifactServiceProtocol, request.app.state.agent_artifact_service)
    try:
        result = await service.get_download(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            artifact_id=artifact_id,
        )
    except AgentArtifactError as error:
        raise _artifact_api_error(error) from error
    return AgentArtifactDownloadResponse(
        artifact_id=result.artifact_id,
        status=result.status,
        content_type=result.content_type,
        content_sha256=result.content_sha256,
        size_bytes=result.size_bytes,
        url=result.url,
        expires_in_seconds=result.expires_in_seconds,
    )


def _artifact_response(result: AgentArtifactResult) -> AgentArtifactResponse:
    return AgentArtifactResponse(
        artifact_id=result.artifact_id,
        run_id=result.run_id,
        document_version_id=result.document_version_id,
        kind=result.kind,
        status=result.status,
        content_type=result.content_type,
        content_sha256=result.content_sha256,
        size_bytes=result.size_bytes,
        created_at=result.created_at,
        verified_at=result.verified_at,
        published_at=result.published_at,
    )


def _artifact_api_error(error: AgentArtifactError) -> ApiError:
    if isinstance(error, AgentArtifactPrincipalForbidden):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, AgentArtifactNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, AgentArtifactIntegrityError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, AgentArtifactStoreUnavailable):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ApiError(status_code=status_code, code=error.code, message=error.message)


__all__ = [
    "AgentArtifactDownloadResponse",
    "AgentArtifactResponse",
    "AgentArtifactServiceProtocol",
    "router",
]
