from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import Field, StrictInt

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import DocumentEnvelopeViolation
from enterprise_doc_core.object_store import (
    MultipartUploadNotFound,
    ObjectStoreError,
    ObjectStoreNotFound,
    ObjectStoreProtocolError,
    ObjectStoreUnavailable,
)
from enterprise_doc_core.uploads import (
    CompleteUploadPartInput,
    CompleteUploadSessionInput,
    CompleteUploadSessionResult,
    CreateUploadSessionInput,
    CreateUploadSessionResult,
    GetUploadSessionResult,
    PresignUploadPartInput,
    PresignUploadPartResult,
    UploadCompletionPartsInvalid,
    UploadCompletionVerificationFailed,
    UploadIdempotencyConflict,
    UploadIdempotencyKeyInvalid,
    UploadInitializationFailed,
    UploadInitializationInProgress,
    UploadPartChecksumConflict,
    UploadPartChecksumInvalid,
    UploadPartNumberInvalid,
    UploadPartSizeInvalid,
    UploadPolicyViolation,
    UploadQuotaExceeded,
    UploadSessionError,
    UploadSessionExpired,
    UploadSessionNotActive,
    UploadSessionNotFound,
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


class UploadSessionServiceProtocol(Protocol):
    async def get(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
    ) -> GetUploadSessionResult: ...

    async def presign_part(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
        part_number: int,
        request: PresignUploadPartInput,
    ) -> PresignUploadPartResult: ...

    async def complete(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
        request: CompleteUploadSessionInput,
    ) -> CompleteUploadSessionResult: ...


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


class UploadedPartResponse(ApiModel):
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256: str


class UploadSessionResponse(ApiModel):
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
    uploaded_parts: list[UploadedPartResponse]


class PresignUploadPartRequest(ApiModel):
    size_bytes: StrictInt
    checksum_sha256: str


class PresignUploadPartResponse(ApiModel):
    part_number: int
    size_bytes: int
    checksum_sha256: str
    url: str
    headers: dict[str, str]
    expires_in_seconds: int


class CompleteUploadPartRequest(ApiModel):
    part_number: StrictInt
    size_bytes: StrictInt
    etag: str
    checksum_sha256: str


class UploadSessionCompleteRequest(ApiModel):
    parts: list[CompleteUploadPartRequest] = Field(min_length=1, max_length=10_000)


class UploadSessionCompleteResponse(ApiModel):
    session_id: UUID
    status: str
    document_id: UUID
    version_id: UUID
    completed_at: datetime
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
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
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
    except (UploadInitializationFailed, UploadInitializationInProgress) as error:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=error.code,
            message=error.message,
        ) from error
    except ObjectStoreError as error:
        raise _object_store_api_error(error) from error

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


@router.get(
    "/{session_id}",
    response_model=UploadSessionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_upload_session(
    session_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> UploadSessionResponse:
    service = cast(UploadSessionServiceProtocol, request.app.state.upload_session_service)
    try:
        result = await service.get(principal=principal, session_id=session_id)
    except UploadSessionError as error:
        raise _upload_session_api_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_api_error(error) from error
    return UploadSessionResponse(
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
        uploaded_parts=[
            UploadedPartResponse(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
                etag=part.etag,
                checksum_sha256=part.checksum_sha256_b64,
            )
            for part in result.uploaded_parts
        ],
    )


@router.post(
    "/{session_id}/parts/{part_number}/presign",
    response_model=PresignUploadPartResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def presign_upload_part(
    session_id: UUID,
    part_number: int,
    payload: PresignUploadPartRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> PresignUploadPartResponse:
    service = cast(UploadSessionServiceProtocol, request.app.state.upload_session_service)
    try:
        result = await service.presign_part(
            principal=principal,
            session_id=session_id,
            part_number=part_number,
            request=PresignUploadPartInput(
                size_bytes=payload.size_bytes,
                checksum_sha256_b64=payload.checksum_sha256,
            ),
        )
    except UploadSessionError as error:
        raise _upload_session_api_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_api_error(error) from error
    return PresignUploadPartResponse(
        part_number=result.part_number,
        size_bytes=result.size_bytes,
        checksum_sha256=result.checksum_sha256_b64,
        url=result.url,
        headers=dict(result.headers),
        expires_in_seconds=result.expires_in_seconds,
    )


@router.post(
    "/{session_id}/complete",
    response_model=UploadSessionCompleteResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def complete_upload_session(
    session_id: UUID,
    payload: UploadSessionCompleteRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> UploadSessionCompleteResponse:
    service = cast(UploadSessionServiceProtocol, request.app.state.upload_session_service)
    try:
        result = await service.complete(
            principal=principal,
            session_id=session_id,
            request=CompleteUploadSessionInput(
                parts=tuple(
                    CompleteUploadPartInput(
                        part_number=part.part_number,
                        size_bytes=part.size_bytes,
                        etag=part.etag,
                        checksum_sha256_b64=part.checksum_sha256,
                    )
                    for part in payload.parts
                )
            ),
        )
    except DocumentEnvelopeViolation as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=error.code,
            message=error.message,
        ) from error
    except UploadSessionError as error:
        raise _upload_session_api_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_api_error(error) from error
    return UploadSessionCompleteResponse(
        session_id=result.session_id,
        status=result.status,
        document_id=result.document_id,
        version_id=result.version_id,
        completed_at=result.completed_at,
        replayed=result.replayed,
    )


def _upload_session_api_error(error: UploadSessionError) -> ApiError:
    if isinstance(
        error,
        (UploadPartNumberInvalid, UploadPartSizeInvalid, UploadPartChecksumInvalid),
    ):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, UploadSessionNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, UploadSessionExpired):
        status_code = status.HTTP_410_GONE
    elif isinstance(
        error,
        (
            UploadSessionNotActive,
            UploadPartChecksumConflict,
            UploadCompletionPartsInvalid,
            UploadCompletionVerificationFailed,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ApiError(status_code=status_code, code=error.code, message=error.message)


def _object_store_api_error(error: ObjectStoreError) -> ApiError:
    if isinstance(error, ObjectStoreUnavailable):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(error, (MultipartUploadNotFound, ObjectStoreNotFound)):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, ObjectStoreProtocolError):
        status_code = status.HTTP_502_BAD_GATEWAY
    else:
        status_code = status.HTTP_502_BAD_GATEWAY
    return ApiError(status_code=status_code, code=error.code, message=error.message)
