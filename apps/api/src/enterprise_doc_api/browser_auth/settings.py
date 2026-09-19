from __future__ import annotations

from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from enterprise_doc_core.config import AppEnvironment


class BrowserAuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool = False
    web_origin: str = "http://localhost:5173"
    issuer: str | None = Field(default=None, max_length=512)
    authorization_endpoint: str | None = Field(default=None, max_length=2048)
    token_endpoint: str | None = Field(default=None, max_length=2048)
    jwks_url: str | None = Field(default=None, max_length=2048)
    client_id: str | None = Field(default=None, max_length=512)
    client_secret: SecretStr | None = None
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    login_ttl_seconds: int = Field(default=300, ge=60, le=600)
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)
    oidc_timeout_seconds: float = Field(default=15, gt=0, le=30)
    clock_leeway_seconds: int = Field(default=30, ge=0, le=60)
    login_limit_per_minute: int = Field(default=30, ge=1, le=300)

    @property
    def redirect_uri(self) -> str:
        return self.web_origin + "/auth/callback"

    @model_validator(mode="after")
    def validate_trust_configuration(self) -> Self:
        values = (
            self.issuer,
            self.authorization_endpoint,
            self.token_endpoint,
            self.jwks_url,
            self.client_id,
        )
        if self.enabled and any(not value for value in values):
            raise ValueError(
                "browser authentication requires explicit OIDC endpoints and client ID"
            )
        for value in (self.web_origin, *values):
            if value is not None and (
                not value
                or value != value.strip()
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            ):
                raise ValueError("browser authentication settings must be exact non-empty values")
        for value in (
            self.web_origin,
            self.issuer,
            self.authorization_endpoint,
            self.token_endpoint,
            self.jwks_url,
        ):
            if value is not None:
                parsed = urlsplit(value)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                    or "\\" in value
                ):
                    raise ValueError("browser authentication URLs must be exact HTTP(S) URLs")
                _ = parsed.port
        origin = urlsplit(self.web_origin)
        if origin.path or self.web_origin != f"{origin.scheme}://{origin.netloc}":
            raise ValueError("browser web origin must not include a path")
        if not self.algorithms or any(a not in {"RS256", "ES256"} for a in self.algorithms):
            raise ValueError("browser authentication requires allowed asymmetric algorithms")
        if self.client_secret is not None and not self.client_secret.get_secret_value():
            raise ValueError("browser client secret must not be empty")
        return self

    def validate_environment(self, environment: AppEnvironment) -> None:
        if not self.enabled:
            return
        local = environment in {AppEnvironment.LOCAL, AppEnvironment.TEST}
        for value in (
            self.web_origin,
            self.issuer,
            self.authorization_endpoint,
            self.token_endpoint,
            self.jwks_url,
        ):
            assert value is not None
            parsed = urlsplit(value)
            if parsed.scheme != "https" and not (
                local and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError("browser authentication requires HTTPS except local loopback")
        if not local and self.client_secret is None:
            raise ValueError("browser authentication requires a confidential client secret")
