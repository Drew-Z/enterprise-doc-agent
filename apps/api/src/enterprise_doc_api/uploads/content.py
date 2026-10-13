from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field, StrictInt, ValidationError

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_api.uploads.router import (
    UploadSessionCompleteResponse,
    UploadSessionCreateRequest,
    UploadSessionCreateResponse,
    _object_store_api_error,
    _upload_session_api_error,
    create_upload_session,
)
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import DocumentEnvelopeViolation
from enterprise_doc_core.object_store import ObjectStoreError
from enterprise_doc_core.object_store.signed_upload import (
    MAX_SIGNED_UPLOAD_BYTES,
    SignedUploadWriter,
)
from enterprise_doc_core.uploads import CompleteUploadSessionResult, UploadSessionError
from enterprise_doc_core.uploads.models import UploadTransport

MAX_CONTENT_BASE64_LENGTH = 4 * ((MAX_SIGNED_UPLOAD_BYTES + 2) // 3)
MAX_CONTENT_WIRE_BYTES = MAX_CONTENT_BASE64_LENGTH + 16_384


class ContentUploadRequest(ApiModel):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    size_bytes: StrictInt = Field(gt=0, le=MAX_SIGNED_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str = Field(min_length=4, max_length=MAX_CONTENT_BASE64_LENGTH)


class ContentUploadResponse(ApiModel):
    session: UploadSessionCreateResponse
    completion: UploadSessionCompleteResponse | None


class ContentCompletionService(Protocol):
    async def complete_content(
        self,
        *,
        principal: PrincipalContext,
        session_id: UUID,
        content: bytes,
        writer: SignedUploadWriter,
    ) -> CompleteUploadSessionResult: ...


router = APIRouter(prefix="/api/upload-sessions", tags=["uploads"])


async def _bounded_payload(request: Request) -> ContentUploadRequest:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ApiError(status_code=415, code="content_type_invalid", message="JSON is required.")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise ApiError(
                status_code=400, code="content_length_invalid", message="Invalid length."
            )
        # Bound even enormous decimal headers before conversion to int.
        normalized_length = length.lstrip("0") or "0"
        if len(normalized_length) > 7 or int(normalized_length) > MAX_CONTENT_WIRE_BYTES:
            raise _wire_limit_error()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CONTENT_WIRE_BYTES:
            raise _wire_limit_error()
        body.extend(chunk)
    try:
        return ContentUploadRequest.model_validate_json(body)
    except ValidationError:
        pass
    # Do not retain validation errors containing the document bytes.
    raise ApiError(
        status_code=422, code="request_validation_failed", message="Invalid upload body."
    )


def _wire_limit_error() -> ApiError:
    return ApiError(
        status_code=413, code="upload_size_exceeded", message="Upload body is too large."
    )


@router.post(
    "/content",
    response_model=ContentUploadResponse,
    response_model_exclude_defaults=True,
    status_code=201,
    responses={
        code: {"model": ErrorResponse}
        for code in (400, 401, 404, 409, 410, 413, 415, 422, 502, 503)
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ContentUploadRequest.model_json_schema(by_alias=True)
                }
            },
        }
    },
)
async def upload_content(
    request: Request,
    response: Response,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ContentUploadResponse:
    if not request.app.state.upload_content_enabled:
        raise ApiError(
            status_code=404, code="upload_content_unsupported", message="Use session upload."
        )
    if idempotency_key is None:
        raise ApiError(
            status_code=400, code="idempotency_key_required", message="Idempotency-Key is required."
        )
    payload = await _bounded_payload(request)
    content: bytes | None = None
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error):
        pass
    if content is None:
        raise ApiError(
            status_code=422, code="request_validation_failed", message="Invalid upload body."
        )
    if len(content) != payload.size_bytes or hashlib.sha256(content).hexdigest() != payload.sha256:
        raise ApiError(
            status_code=400,
            code="upload_content_mismatch",
            message="File size or SHA256 does not match.",
        )
    session = await create_upload_session(
        payload=UploadSessionCreateRequest(
            filename=payload.filename,
            size_bytes=payload.size_bytes,
            media_type=payload.media_type,
            sha256=payload.sha256,
            transport=UploadTransport.SINGLE_PUT,
        ),
        request=request,
        response=response,
        principal=principal,
        idempotency_key=idempotency_key,
        include_signature=False,
    )
    response.headers["Cache-Control"] = "no-store"
    # An older same-key session created with the capability disabled retains
    # its persisted multipart transport. The client continues that exact session.
    if session.transport != UploadTransport.SINGLE_PUT:
        return ContentUploadResponse(session=session, completion=None)
    service = cast(ContentCompletionService, request.app.state.upload_session_service)
    writer = cast(SignedUploadWriter, request.app.state.signed_upload_writer)
    try:
        result = await service.complete_content(
            principal=principal,
            session_id=session.session_id,
            content=content,
            writer=writer,
        )
    except DocumentEnvelopeViolation as error:
        raise ApiError(status_code=409, code=error.code, message=error.message) from error
    except UploadSessionError as error:
        raise _upload_session_api_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_api_error(error) from error
    return ContentUploadResponse(
        session=session,
        completion=UploadSessionCompleteResponse(
            session_id=result.session_id,
            status=result.status,
            document_id=result.document_id,
            version_id=result.version_id,
            completed_at=result.completed_at,
            replayed=result.replayed,
        ),
    )
