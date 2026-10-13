from collections.abc import Sequence
from datetime import datetime

from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall


def summarize_calls(operation: PresalesAttempt, calls: Sequence[PresalesProviderCall]) -> None:
    operation.provider_request_count = (
        None
        if any(c.state in {"running", "unknown"} for c in calls)
        else sum(c.state != "not_sent" for c in calls)
    )
    operation.usage = None
    if calls and all(c.usage is not None for c in calls):
        operation.usage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [c.usage.get(key) if c.usage else None for c in calls]
            operation.usage[key] = (
                sum(v for v in values if v is not None)
                if all(v is not None for v in values)
                else None
            )


def abandon_calls(calls: Sequence[PresalesProviderCall], now: datetime) -> None:
    for call in calls:
        if call.state == "running":
            call.state, call.finished_at = "unknown", now
            call.error_code, call.retryable = "presales_dispatch_unobserved", True
