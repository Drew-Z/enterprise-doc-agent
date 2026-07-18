from __future__ import annotations

from pydantic import BaseModel, Field

from enterprise_doc_core.config import FoundationSettings


class WorkerServerSettings(BaseModel):
    host: str = "127.0.0.1"
    probe_port: int = Field(default=8081, ge=1, le=65535)
    worker_id: str = Field(default="worker-local", min_length=1, max_length=200)
    publisher_batch_size: int = Field(default=20, ge=1, le=100)
    publisher_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)


class WorkerSettings(FoundationSettings):
    worker: WorkerServerSettings = Field(default_factory=WorkerServerSettings)
