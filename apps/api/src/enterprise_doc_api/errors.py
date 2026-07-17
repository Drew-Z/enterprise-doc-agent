from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from enterprise_doc_core.context import get_request_context

_LOGGER = logging.getLogger("enterprise_doc_api.errors")


class ErrorDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    request_id: str | None = Field(default=None, alias="requestId")


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


def api_error_response(error: ApiError) -> JSONResponse:
    context = get_request_context()
    response = ErrorResponse(
        error=ErrorDetail(
            code=error.code,
            message=error.message,
            requestId=context.request_id if context is not None else None,
        )
    )
    return JSONResponse(
        status_code=error.status_code,
        content=response.model_dump(mode="json", by_alias=True),
        headers=error.headers,
    )


def unexpected_error_response(error: Exception) -> JSONResponse:
    _LOGGER.error(
        "unhandled_api_exception",
        extra={"event_data": {"error_type": type(error).__name__}},
    )
    return api_error_response(
        ApiError(
            status_code=500,
            code="internal_error",
            message="The request could not be completed.",
        )
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return api_error_response(error)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request,
        __: RequestValidationError,
    ) -> JSONResponse:
        context = get_request_context()
        response = ErrorResponse(
            error=ErrorDetail(
                code="request_validation_failed",
                message="The request payload is invalid.",
                requestId=context.request_id if context is not None else None,
            )
        )
        return JSONResponse(
            status_code=422,
            content=response.model_dump(mode="json", by_alias=True),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if error.status_code == 404:
            code = "http_not_found"
            message = "The requested resource was not found."
        elif error.status_code == 405:
            code = "http_method_not_allowed"
            message = "The request method is not allowed for this resource."
        else:
            code = "http_error"
            message = "The request could not be completed."
        context = get_request_context()
        response = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                requestId=context.request_id if context is not None else None,
            )
        )
        return JSONResponse(
            status_code=error.status_code,
            content=response.model_dump(mode="json", by_alias=True),
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, error: Exception) -> JSONResponse:
        return unexpected_error_response(error)
