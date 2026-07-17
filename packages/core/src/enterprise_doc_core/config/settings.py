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
    access_key: SecretStr = SecretStr("enterprise_doc_local")
    secret_key: SecretStr = SecretStr("enterprise_doc_local_secret")
    region: str = "us-east-1"
    documents_bucket: str = "documents"
    artifacts_bucket: str = "artifacts"
    secure: bool = False
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)


class ObservabilitySettings(BaseModel):
    enabled: bool = False
    exporter_otlp_endpoint: str = "http://127.0.0.1:4318"
    sample_ratio: float = Field(default=1.0, ge=0, le=1)


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
    otel: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @model_validator(mode="after")
    def reject_development_credentials_outside_local(self) -> Self:
        if self.app_env is AppEnvironment.LOCAL:
            return self

        sensitive_values = (
            self.database.url.get_secret_value(),
            self.object_store.access_key.get_secret_value(),
            self.object_store.secret_key.get_secret_value(),
        )
        if any("enterprise_doc_local" in value for value in sensitive_values):
            raise ValueError("development credentials are forbidden outside the local environment")
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Return a JSON-compatible representation with secret values redacted."""
        return cast(dict[str, Any], json.loads(self.model_dump_json()))
