from __future__ import annotations

import math
import threading
import time
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SAMPLE_AGE_MS = 45_000
MAX_FUTURE_SKEW_MS = 15_000
MAX_SAFE_TIMESTAMP = 2**53 - 1


class QueueObservation(BaseModel):
    """Safe aggregate only; no queue contents, identities or connection counts."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    healthy: bool = False
    source_at: int | None = Field(default=None, ge=1, le=MAX_SAFE_TIMESTAMP)

    @model_validator(mode="after")
    def require_success_timestamp(self) -> Self:
        if self.healthy and self.source_at is None:
            raise ValueError("queue_success_requires_source_time")
        return self

    def at(self, now: float) -> QueueObservation:
        if (
            self.source_at is None
            or not math.isfinite(now)
            or not -MAX_FUTURE_SKEW_MS <= now * 1000 - self.source_at <= MAX_SAMPLE_AGE_MS
        ):
            return self.model_copy(update={"healthy": False})
        return self


class ResourceHealthState:
    """Keep completed samples atomic even when probes and sampling use different threads."""

    def __init__(self) -> None:
        self._started_at = time.time()
        self._lock = threading.Lock()
        self._samples: dict[str, tuple[float | None, float]] = {}

    def record(self, source: str, value: float | None, observed_at: float) -> None:
        # MetricsRuntime validates successful observations before this call. A
        # failed sample retains its last success time but invalidates its value.
        with self._lock:
            previous = self._samples.get(source, (None, 0.0))
            self._samples[source] = (value, observed_at if value is not None else previous[1])

    def projection(self, now: float) -> QueueObservation:
        with self._lock:
            samples = [self._samples.get(source, (None, 0.0)) for source in ("queue", "redis")]
        times = [sample[1] for sample in samples]
        if any(
            not math.isfinite(stamp) or not 1 <= stamp * 1000 <= MAX_SAFE_TIMESTAMP
            for stamp in times
        ):
            return QueueObservation()
        healthy = (
            all(value is not None for value, _ in samples)
            and all(stamp >= self._started_at for stamp in times)
            and math.isfinite(now)
            and all(
                -MAX_FUTURE_SKEW_MS <= (now - stamp) * 1000 <= MAX_SAMPLE_AGE_MS for stamp in times
            )
            and samples[0][0] is not None
            and samples[0][0] <= 120
        )
        return QueueObservation(healthy=healthy, source_at=int(min(times) * 1000)).at(now)
