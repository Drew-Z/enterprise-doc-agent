from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, SecretStr, model_validator

from enterprise_doc_core.config import AppEnvironment, FoundationSettings


class ApiServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    readiness_cache_ttl_seconds: float = Field(default=2.0, ge=0, le=60)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )


class AuthSettings(BaseModel):
    issuer: str = "enterprise-doc-agent-local"
    audience: str = "enterprise-doc-agent-api"
    signing_key: SecretStr = SecretStr("enterprise_doc_local_jwt_signing_key_change_me_32_bytes")
    token_ttl_seconds: int = Field(default=8 * 60 * 60, ge=60, le=7 * 24 * 60 * 60)
    max_token_length: int = Field(default=4096, ge=256, le=16384)


class ApiSettings(FoundationSettings):
    api: ApiServerSettings = Field(default_factory=ApiServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @model_validator(mode="after")
    def reject_development_auth_key_outside_local_or_test(self) -> Self:
        if self.app_env in {AppEnvironment.LOCAL, AppEnvironment.TEST}:
            return self
        if "enterprise_doc_local" in self.auth.signing_key.get_secret_value():
            raise ValueError("development JWT signing key is forbidden outside local/test")
        return self
