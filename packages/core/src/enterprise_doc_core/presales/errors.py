from __future__ import annotations


class PresalesError(Exception):
    """Public code only; never expose provider errors or source content."""

    def __init__(
        self,
        code: str,
        *,
        provider_requests: int = 0,
        retryable: bool = False,
        usage: dict[str, int | None] | None = None,
        provider_response_id: str | None = None,
    ) -> None:
        self.code = code
        self.provider_requests = provider_requests
        self.retryable = retryable
        self.usage = usage
        self.provider_response_id = provider_response_id
        super().__init__(code)
