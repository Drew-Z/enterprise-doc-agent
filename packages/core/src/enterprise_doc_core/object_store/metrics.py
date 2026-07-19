from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from time import perf_counter
from typing import Any, Concatenate, ParamSpec, TypeVar, cast

from enterprise_doc_core.object_store.errors import (
    MultipartUploadNotFound,
    ObjectStoreError,
    ObjectStoreNotFound,
    ObjectStoreUnavailable,
)
from enterprise_doc_core.telemetry import MetricsRuntime

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")


def instrument_object_store_operation(
    operation: str,
) -> Callable[
    [Callable[Concatenate[Any, _Parameters], Coroutine[Any, Any, _Result]]],
    Callable[Concatenate[Any, _Parameters], Coroutine[Any, Any, _Result]],
]:
    """Record one public object-store operation with finite result labels."""

    def decorate(
        function: Callable[Concatenate[Any, _Parameters], Coroutine[Any, Any, _Result]],
    ) -> Callable[Concatenate[Any, _Parameters], Coroutine[Any, Any, _Result]]:
        @wraps(function)
        async def wrapped(
            instance: Any,
            *args: _Parameters.args,
            **kwargs: _Parameters.kwargs,
        ) -> _Result:
            metrics: MetricsRuntime | None = getattr(instance, "metrics", None)
            started = perf_counter()
            result_label = "error"
            try:
                result = await function(instance, *args, **kwargs)
            except asyncio.CancelledError:
                result_label = "cancelled"
                raise
            except (MultipartUploadNotFound, ObjectStoreNotFound):
                result_label = "not_found"
                raise
            except ObjectStoreUnavailable:
                result_label = "retryable_error"
                raise
            except ObjectStoreError:
                result_label = "permanent_error"
                raise
            except Exception:
                result_label = "error"
                raise
            else:
                result_label = "success"
                return result
            finally:
                if metrics is not None:
                    metrics.observe_boundary(
                        boundary="object_store",
                        operation=operation,
                        result=result_label,
                        duration=perf_counter() - started,
                    )

        return cast(
            Callable[Concatenate[Any, _Parameters], Coroutine[Any, Any, _Result]],
            wrapped,
        )

    return decorate


__all__ = ["instrument_object_store_operation"]
