from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class PresalesSettings(BaseModel):
    generation_enabled: bool = False
    model_route: Literal["primary", "fallback"] = "primary"
    model_timeout_seconds: float | None = Field(default=None, gt=0, le=180)
    row_timeout_seconds: float = Field(default=90, gt=0, le=180)
    daily_attempt_limit: int = Field(default=100, ge=1, le=1000)
    concurrent_attempt_limit: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def validate_wait_budget(self) -> Self:
        if (
            self.model_timeout_seconds is not None
            and self.model_timeout_seconds >= self.row_timeout_seconds
        ):
            raise ValueError("model_timeout_seconds must be less than row_timeout_seconds")
        return self
