from pydantic import BaseModel, Field


class PresalesSettings(BaseModel):
    generation_enabled: bool = False
    row_timeout_seconds: float = Field(default=90, gt=0, le=180)
    daily_attempt_limit: int = Field(default=100, ge=1, le=1000)
    concurrent_attempt_limit: int = Field(default=2, ge=1, le=4)
