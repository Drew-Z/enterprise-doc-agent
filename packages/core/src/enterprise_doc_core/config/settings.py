from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Self, cast

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ModelProvider(StrEnum):
    DETERMINISTIC = "deterministic"
    OPENAI_COMPATIBLE = "openai_compatible"


class DatabaseSettings(BaseModel):
    url: SecretStr = SecretStr(
        "postgresql+psycopg://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc"
    )
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)


class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)


class ObjectStoreSettings(BaseModel):
    endpoint: str = "http://127.0.0.1:9000"
    presign_endpoint: str = "http://127.0.0.1:9000"
    access_key: SecretStr = SecretStr("enterprise_doc_local")
    secret_key: SecretStr = SecretStr("enterprise_doc_local_secret")
    region: str = "us-east-1"
    documents_bucket: str = "documents"
    artifacts_bucket: str = "artifacts"
    secure: bool = False
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_pool_connections: int = Field(default=32, ge=1, le=256)
    presign_ttl_seconds: int = Field(default=900, ge=60, le=3600)


class UploadSettings(BaseModel):
    max_file_size_bytes: int = Field(default=10 * 1024**3, gt=0, le=5 * 1024**4)
    max_filename_length: int = Field(default=255, ge=1, le=255)
    preferred_part_size_bytes: int = Field(
        default=16 * 1024**2,
        ge=5 * 1024**2,
        le=5 * 1024**3,
    )
    session_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60, le=7 * 24 * 60 * 60)
    envelope_sample_bytes: int = Field(default=64 * 1024, ge=4, le=1024 * 1024)
    max_docx_central_directory_bytes: int = Field(
        default=4 * 1024**2,
        ge=46,
        le=64 * 1024**2,
    )
    max_docx_entries: int = Field(default=10_000, ge=2, le=100_000)
    max_docx_declared_uncompressed_bytes: int = Field(
        default=2 * 1024**3,
        gt=0,
        le=10 * 1024**3,
    )
    max_docx_member_uncompressed_bytes: int = Field(
        default=512 * 1024**2,
        gt=0,
        le=2 * 1024**3,
    )
    max_docx_member_compression_ratio: float = Field(
        default=100.0,
        ge=1.0,
        le=10_000.0,
    )
    cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    cleanup_expiry_grace_seconds: int = Field(default=60, ge=0, le=24 * 60 * 60)
    cleanup_completing_grace_seconds: int = Field(
        default=15 * 60,
        ge=60,
        le=7 * 24 * 60 * 60,
    )
    cleanup_orphan_grace_seconds: int = Field(
        default=60 * 60,
        ge=60,
        le=30 * 24 * 60 * 60,
    )
    cleanup_claim_ttl_seconds: int = Field(default=5 * 60, ge=30, le=60 * 60)


class ObservabilitySettings(BaseModel):
    enabled: bool = False
    exporter_otlp_endpoint: str = "http://127.0.0.1:4318"
    sample_ratio: float = Field(default=1.0, ge=0, le=1)


class AgentSettings(BaseModel):
    graph_version: str = Field(default="m4.v1", min_length=1, max_length=64)
    prompt_version: str = Field(default="m4.v1", min_length=1, max_length=64)
    tool_schema_version: str = Field(default="m4.v1", min_length=1, max_length=64)
    checkpoint_url: SecretStr | None = None
    checkpoint_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    strict_msgpack: bool = True

    @model_validator(mode="after")
    def require_strict_msgpack(self) -> Self:
        if not self.strict_msgpack:
            raise ValueError("strict msgpack cannot be disabled")
        return self


class ModelSettings(BaseModel):
    provider: ModelProvider = ModelProvider.DETERMINISTIC
    base_url: str | None = None
    api_key: SecretStr | None = None
    model_name: str | None = None
    model_version: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_bytes: int = Field(default=256 * 1024, ge=1024, le=4 * 1024**2)

    @model_validator(mode="after")
    def require_openai_compatible_configuration(self) -> Self:
        if self.provider is ModelProvider.DETERMINISTIC:
            return self
        if self.base_url is None or not self.base_url.strip():
            raise ValueError("OpenAI-compatible model provider requires a base URL")
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError("OpenAI-compatible model provider requires an API key")
        if self.model_name is None or not self.model_name.strip():
            raise ValueError("OpenAI-compatible model provider requires a model name")
        return self


class McpSettings(BaseModel):
    command: str = Field(default="enterprise-doc-mcp", min_length=1, max_length=512)
    signing_secret: SecretStr = SecretStr(
        "enterprise_doc_local_mcp_signing_secret_change_me_32_bytes"
    )
    context_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_message_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024**2)

    @model_validator(mode="after")
    def require_signing_secret_strength(self) -> Self:
        if len(self.signing_secret.get_secret_value()) < 32:
            raise ValueError("MCP signing secret must be at least 32 bytes")
        return self


class FoundationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnvironment = AppEnvironment.LOCAL
    log_level: str = "INFO"
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    object_store: ObjectStoreSettings = Field(default_factory=ObjectStoreSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    otel: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)

    @model_validator(mode="after")
    def reject_development_credentials_outside_local(self) -> Self:
        if self.app_env is not AppEnvironment.LOCAL:
            sensitive_values = (
                self.database.url.get_secret_value(),
                self.object_store.access_key.get_secret_value(),
                self.object_store.secret_key.get_secret_value(),
            )
            if any("enterprise_doc_local" in value for value in sensitive_values):
                raise ValueError(
                    "development credentials are forbidden outside the local environment"
                )
        if self.app_env in {AppEnvironment.LOCAL, AppEnvironment.TEST}:
            return self
        if self.model.provider is ModelProvider.DETERMINISTIC:
            raise ValueError("deterministic model provider is forbidden outside local/test")
        if "enterprise_doc_local" in self.mcp.signing_secret.get_secret_value():
            raise ValueError("development MCP signing secret is forbidden outside local/test")
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Return a JSON-compatible representation with secret values redacted."""
        return cast(dict[str, Any], json.loads(self.model_dump_json()))
