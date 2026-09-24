"""Small allowlisted provider identifiers; never persist arbitrary response metadata."""

import re

import httpx

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")


def safe_provider_id(value: object) -> str | None:
    # Reject instead of truncating: truncated IDs could join unrelated requests.
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def provider_request_id(headers: httpx.Headers) -> str | None:
    for name in ("x-request-id", "request-id"):
        identifier = safe_provider_id(headers.get(name))
        if identifier is not None:
            return identifier
    return None
