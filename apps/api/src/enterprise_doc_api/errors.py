from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from enterprise_doc_core.context import get_request_context


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


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
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


def error_response_schema() -> dict[str, Any]:
    return ErrorResponse.model_json_schema(by_alias=True)
