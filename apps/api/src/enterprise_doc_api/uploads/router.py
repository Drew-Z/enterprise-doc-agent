from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import StrictInt

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.uploads import (
    CreateUploadSessionInput,
    CreateUploadSessionResult,
    UploadIdempotencyConflict,
    UploadIdempotencyKeyInvalid,
    UploadPolicyViolation,
    UploadQuotaExceeded,
    UploadTenantUnavailable,
)


class UploadCreationServiceProtocol(Protocol):
    async def create(
        self,
        *,
        principal: PrincipalContext,
        idempotency_key: str,
        request: CreateUploadSessionInput,
    ) -> CreateUploadSessionResult: ...


class UploadSessionCreateRequest(ApiModel):
    filename: str
    size_bytes: StrictInt
    media_type: str
    sha256: str


class UploadSessionCreateResponse(ApiModel):
    session_id: UUID
    status: str
    filename: str
    extension: str
    media_type: str
    size_bytes: int
    declared_sha256: str
    part_size_bytes: int
    expected_part_count: int
    expires_at: datetime
    replayed: bool


router = APIRouter(prefix="/api/upload-sessions", tags=["uploads"])


@router.post(
    "",
    response_model=UploadSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": UploadSessionCreateResponse,
            "description": "Idempotent replay of an existing upload session.",
        },
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_upload_session(
    payload: UploadSessionCreateRequest,
    response: Response,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadSessionCreateResponse:
    if idempotency_key is None:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="idempotency_key_required",
            message="An Idempotency-Key header is required.",
        )
    service = cast(UploadCreationServiceProtocol, request.app.state.upload_creation_service)
    try:
        result = await service.create(
            principal=principal,
            idempotency_key=idempotency_key,
            request=CreateUploadSessionInput(
                filename=payload.filename,
                size_bytes=payload.size_bytes,
                media_type=payload.media_type,
                sha256=payload.sha256,
            ),
        )
    except UploadPolicyViolation as error:
        raise ApiError(
            status_code=(
                status.HTTP_413_CONTENT_TOO_LARGE
                if error.code == "upload_size_exceeded"
                else status.HTTP_400_BAD_REQUEST
            ),
            code=error.code,
            message=error.message,
        ) from error
    except UploadIdempotencyKeyInvalid as error:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=error.code,
            message=error.message,
        ) from error
    except UploadIdempotencyConflict as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=error.code,
            message=error.message,
        ) from error
    except UploadQuotaExceeded as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=error.code,
            message=error.message,
        ) from error
    except UploadTenantUnavailable as error:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=error.code,
            message=error.message,
        ) from error

    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return UploadSessionCreateResponse(
        session_id=result.session_id,
        status=result.status,
        filename=result.filename,
        extension=result.extension,
        media_type=result.media_type,
        size_bytes=result.size_bytes,
        declared_sha256=result.declared_sha256,
        part_size_bytes=result.part_size_bytes,
        expected_part_count=result.expected_part_count,
        expires_at=result.expires_at,
        replayed=result.replayed,
    )
