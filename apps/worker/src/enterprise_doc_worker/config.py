from __future__ import annotations

from pydantic import BaseModel, Field

from enterprise_doc_core.config import FoundationSettings


class WorkerServerSettings(BaseModel):
    host: str = "127.0.0.1"
    probe_port: int = Field(default=8081, ge=1, le=65535)


class WorkerSettings(FoundationSettings):
    worker: WorkerServerSettings = Field(default_factory=WorkerServerSettings)
