from __future__ import annotations

from pydantic import BaseModel, Field

from enterprise_doc_core.config import FoundationSettings


class ApiServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )


class ApiSettings(FoundationSettings):
    api: ApiServerSettings = Field(default_factory=ApiServerSettings)
