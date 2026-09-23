from pydantic import BaseModel, Field


class DemoSettings(BaseModel):
    enabled: bool = False
    session_ttl_seconds: int = Field(default=7200, ge=300, le=7200)
    max_workspaces: int = Field(default=6, ge=1, le=20)
    daily_workspace_limit: int = Field(default=24, ge=1, le=100)
    daily_attempt_limit: int = Field(default=40, ge=1, le=100)


ATTEMPT_LIMIT = 6
UPLOAD_LIMIT = 6
FILE_SIZE_LIMIT = 2 * 1024 * 1024
STORAGE_LIMIT = 10 * 1024 * 1024
PACKET_LIMIT = 3
ROW_LIMIT = 6
# Longer than every permitted presigned URL and bounded API generation request.
CLEANUP_GRACE_SECONDS = 3600


class DemoError(Exception):
    def __init__(self, code: str, status: int = 429) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
