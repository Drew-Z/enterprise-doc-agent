from __future__ import annotations

from typing import Self
from uuid import UUID

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
    # External IdP verification is deliberately adapter-driven. The default
    # remains local JWT until an application injects an ExternalPrincipalResolver.
    external_auth_enabled: bool = False
    external_issuer: str | None = Field(default=None, min_length=1, max_length=512)
    external_audience: str | None = Field(default=None, min_length=1, max_length=512)
    external_jwks_url: str | None = Field(default=None, min_length=1, max_length=2048)
    external_tenant_claim: str = Field(default="tenant_id", min_length=1, max_length=128)
    external_actor_claim: str = Field(default="actor_id", min_length=1, max_length=128)
    external_groups_claim: str = Field(default="groups", min_length=1, max_length=128)
    external_role_claim: str = Field(default="role", min_length=1, max_length=128)
    external_role_claim_enabled: bool = False
    external_owner_groups: tuple[str, ...] = ("owner", "tenant-owner")
    external_member_groups: tuple[str, ...] = ("member", "tenant-member")
    scim_enabled: bool = False
    scim_issuer: str | None = Field(default=None, min_length=1, max_length=512)
    scim_tenant_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    external_algorithms: tuple[str, ...] = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

    @model_validator(mode="after")
    def require_external_trust_configuration(self) -> Self:
        if self.external_issuer is not None:
            self.external_issuer = self.external_issuer.strip()
            if not self.external_issuer:
                raise ValueError("external issuer must not be blank")
        if self.external_audience is not None:
            self.external_audience = self.external_audience.strip()
            if not self.external_audience:
                raise ValueError("external audience must not be blank")
        if self.external_auth_enabled and (not self.external_issuer or not self.external_audience):
            raise ValueError("external authentication requires an issuer and audience")
        if not self.external_algorithms or any(
            algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
            for algorithm in self.external_algorithms
        ):
            raise ValueError("external authentication algorithms are not allowed")
        for setting_name, groups in (
            ("external_owner_groups", self.external_owner_groups),
            ("external_member_groups", self.external_member_groups),
        ):
            if any(
                not group
                or group != group.strip()
                or len(group) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in group)
                for group in groups
            ):
                raise ValueError(f"{setting_name} entries must be trimmed, non-empty group names")
            if len(groups) != len(set(groups)):
                raise ValueError(f"{setting_name} contains duplicate group names")
        if set(self.external_owner_groups) & set(self.external_member_groups):
            raise ValueError("external owner and member groups must not overlap")
        if (
            self.external_auth_enabled
            and not self.external_role_claim_enabled
            and not self.external_owner_groups
            and not self.external_member_groups
        ):
            raise ValueError("external authentication requires at least one role mapping")
        if self.scim_issuer is not None:
            self.scim_issuer = self.scim_issuer.strip()
            if not self.scim_issuer:
                raise ValueError("SCIM issuer must not be blank")
        if self.scim_enabled and not self.scim_issuer:
            raise ValueError("SCIM requires an issuer")
        if len(self.scim_tenant_tokens) > 100:
            raise ValueError("SCIM supports at most 100 tenant tokens")
        normalized_tokens: dict[str, SecretStr] = {}
        for tenant_key, token in self.scim_tenant_tokens.items():
            try:
                normalized_tenant_key = str(UUID(tenant_key))
            except (TypeError, ValueError) as error:
                raise ValueError("SCIM tenant token keys must be UUIDs") from error
            if normalized_tenant_key in normalized_tokens:
                raise ValueError("SCIM tenant token keys must be unique")
            token_value = token.get_secret_value()
            if len(token_value) < 32:
                raise ValueError("SCIM tenant tokens must be at least 32 bytes")
            if token_value != token_value.strip() or any(
                ord(character) < 32 or ord(character) == 127 for character in token_value
            ):
                raise ValueError(
                    "SCIM tenant tokens must not contain whitespace or control characters"
                )
            normalized_tokens[normalized_tenant_key] = token
        self.scim_tenant_tokens = normalized_tokens
        if self.scim_enabled and not self.scim_tenant_tokens:
            raise ValueError("SCIM requires at least one tenant token")
        return self


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
