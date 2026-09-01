from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import model_validator

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.documents import (
    Document,
    DocumentAccessMode,
    DocumentGrantInvalid,
    DocumentGrantResult,
    DocumentInventoryItemResult,
    DocumentPolicyError,
    DocumentPolicyForbidden,
    DocumentPolicyNotFound,
)


class DocumentInventoryServiceProtocol(Protocol):
    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        limit: int = 100,
    ) -> tuple[DocumentInventoryItemResult, ...]: ...


class DocumentPolicyServiceProtocol(Protocol):
    async def get_document(
        self, *, tenant_id: UUID, actor_id: UUID, document_id: UUID
    ) -> Document: ...

    async def set_access_mode(self, **kwargs: object) -> Document: ...

    async def list_grants(self, **kwargs: object) -> tuple[DocumentGrantResult, ...]: ...

    async def add_grant(self, **kwargs: object) -> DocumentGrantResult: ...

    async def remove_grant(self, **kwargs: object) -> None: ...


class DocumentInventoryItemResponse(ApiModel):
    document_id: UUID
    title: str
    access_mode: str
    can_manage: bool
    version_id: UUID
    version_number: int
    filename: str
    media_type: str
    size_bytes: int
    version_status: str
    generation_id: UUID | None
    ingestion_status: str | None
    ingestion_stage: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentAccessResponse(ApiModel):
    document_id: UUID
    access_mode: str
    can_manage: bool


class DocumentAccessUpdateRequest(ApiModel):
    access_mode: DocumentAccessMode


class DocumentGrantResponse(ApiModel):
    grant_id: UUID
    document_id: UUID
    grantee_user_id: UUID | None
    grantee_role: str | None


class DocumentGrantCreateRequest(ApiModel):
    grantee_user_id: UUID | None = None
    grantee_role: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> DocumentGrantCreateRequest:
        if (self.grantee_user_id is None) == (self.grantee_role is None):
            raise ValueError("exactly one grant target is required")
        return self


@router.get(
    "",
    response_model=list[DocumentInventoryItemResponse],
    responses={401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def list_document_versions(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[DocumentInventoryItemResponse]:
    service = cast(
        DocumentInventoryServiceProtocol,
        request.app.state.document_inventory_service,
    )
    versions = await service.list_versions(
        tenant_id=UUID(principal.tenant_id),
        actor_id=UUID(principal.actor_id),
        role=principal.role,
        limit=limit,
    )
    return [
        DocumentInventoryItemResponse.model_validate(version, from_attributes=True)
        for version in versions
    ]


@router.get(
    "/{document_id}/access",
    response_model=DocumentAccessResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_document_access(
    document_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> DocumentAccessResponse:
    service = cast(DocumentPolicyServiceProtocol, request.app.state.document_policy_service)
    try:
        document = await service.get_document(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            document_id=document_id,
        )
    except DocumentPolicyError as error:
        raise _policy_api_error(error) from error
    return _access_response(document, principal=principal)


@router.put(
    "/{document_id}/access",
    response_model=DocumentAccessResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_document_access(
    document_id: UUID,
    payload: DocumentAccessUpdateRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> DocumentAccessResponse:
    service = cast(DocumentPolicyServiceProtocol, request.app.state.document_policy_service)
    context = get_request_context()
    try:
        document = await service.set_access_mode(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            document_id=document_id,
            access_mode=payload.access_mode.value,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except DocumentPolicyError as error:
        raise _policy_api_error(error) from error
    return _access_response(document, principal=principal)


@router.get(
    "/{document_id}/grants",
    response_model=list[DocumentGrantResponse],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def list_document_grants(
    document_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[DocumentGrantResponse]:
    service = cast(DocumentPolicyServiceProtocol, request.app.state.document_policy_service)
    try:
        grants = await service.list_grants(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            document_id=document_id,
        )
    except DocumentPolicyError as error:
        raise _policy_api_error(error) from error
    return [_grant_response(grant) for grant in grants]


@router.post(
    "/{document_id}/grants",
    response_model=DocumentGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def add_document_grant(
    document_id: UUID,
    payload: DocumentGrantCreateRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> DocumentGrantResponse:
    service = cast(DocumentPolicyServiceProtocol, request.app.state.document_policy_service)
    context = get_request_context()
    try:
        grant = await service.add_grant(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            document_id=document_id,
            grantee_user_id=payload.grantee_user_id,
            grantee_role=payload.grantee_role,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except DocumentPolicyError as error:
        raise _policy_api_error(error) from error
    return _grant_response(grant)


@router.delete(
    "/{document_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def remove_document_grant(
    document_id: UUID,
    grant_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> Response:
    service = cast(DocumentPolicyServiceProtocol, request.app.state.document_policy_service)
    context = get_request_context()
    try:
        await service.remove_grant(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            document_id=document_id,
            grant_id=grant_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except DocumentPolicyError as error:
        raise _policy_api_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _access_response(document: Document, *, principal: PrincipalContext) -> DocumentAccessResponse:
    return DocumentAccessResponse(
        document_id=document.id,
        access_mode=document.access_mode,
        can_manage=(str(document.created_by) == principal.actor_id or principal.role == "owner"),
    )


def _grant_response(grant: DocumentGrantResult) -> DocumentGrantResponse:
    return DocumentGrantResponse(
        grant_id=grant.grant_id,
        document_id=grant.document_id,
        grantee_user_id=grant.grantee_user_id,
        grantee_role=grant.grantee_role,
    )


def _policy_api_error(error: DocumentPolicyError) -> ApiError:
    if isinstance(error, DocumentPolicyForbidden):
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=error.code,
            message="The current principal cannot manage this document policy.",
        )
    if isinstance(error, DocumentGrantInvalid):
        return ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=error.code,
            message="The document grant target is invalid.",
        )
    if isinstance(error, DocumentPolicyNotFound):
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=error.code,
            message="The document policy was not found.",
        )
    return ApiError(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=error.code,
        message="The document policy request could not be completed.",
    )
