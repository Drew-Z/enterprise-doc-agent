from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

ExecutionMode = Literal["auto", "deep"]
RouteName = Literal["primary", "fallback"]
MAX_POLICY_ROW_SECONDS = 900
DEEP_ROW_SECONDS = 660
DEEP_MODEL_SECONDS = 300


class PolicyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
        allow_inf_nan=False,
        frozen=True,
    )


class RoutePolicy(PolicyModel):
    route: RouteName
    provider: Literal["openai_compatible"]
    endpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str | None = None
    model_revision: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    streaming: bool
    timeout_seconds: float = Field(gt=0, le=300)
    max_output_bytes: int = Field(ge=1024, le=4 * 1024**2, strict=True)
    prompt_version: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionPolicy(PolicyModel):
    version: Literal["presales.execution.v1"] = "presales.execution.v1"
    mode: ExecutionMode
    row_timeout_seconds: float = Field(gt=0, le=MAX_POLICY_ROW_SECONDS)
    queue_timeout_seconds: float = Field(ge=30, le=3600)
    max_provider_requests: int = Field(ge=1, le=2, strict=True)
    daily_dispatch_limit: int = Field(ge=1, le=10000, strict=True)
    routes: list[RoutePolicy] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def bounded_routes(self) -> Self:
        if len({route.route for route in self.routes}) != len(self.routes):
            raise ValueError("execution routes must be unique")
        if self.max_provider_requests != len(self.routes):
            raise ValueError("dispatch count must match the frozen routes")
        return self
