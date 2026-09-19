from __future__ import annotations


class PresalesError(Exception):
    """Public code only; never expose provider errors or source content."""

    def __init__(self, code: str, *, provider_requests: int = 0) -> None:
        self.code = code
        self.provider_requests = provider_requests
        super().__init__(code)
