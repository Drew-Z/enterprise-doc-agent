"""Explicit staging plan for a pod-local API and fixed HTTPS R2 object origins.

The operator still binds the actual origins, namespace, quota, lifetime and provider
budgets in the execution envelope. This module does not connect or grant admission.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from scripts.business_capacity import BusinessPlan


class StagingBusinessPlan(BusinessPlan):
    def object_origin(self, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", parsed.netloc)
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("staging_object_origin_rejected")
        return f"https://{parsed.netloc}"


class PublicStagingBusinessPlan(StagingBusinessPlan):
    """Explicit public-path runner; the execution envelope must bind this approved origin."""

    approved_api_origin: str

    def api_origin(self, value: str) -> str:
        def canonical(origin: str) -> str:
            parsed = urlsplit(origin)
            if (
                any(char.isspace() for char in origin)
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.netloc != parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("public_staging_api_origin_rejected")
            return f"https://{parsed.hostname}"

        selected = canonical(value)
        if selected != canonical(self.approved_api_origin):
            raise ValueError("public_staging_api_origin_mismatch")
        return selected
