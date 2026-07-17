from __future__ import annotations

import pytest

from enterprise_doc_api.auth import (
    BootstrapNotAllowed,
    ensure_bootstrap_allowed,
    normalize_email,
    normalize_slug,
)
from enterprise_doc_core.config import AppEnvironment


@pytest.mark.parametrize("environment", [AppEnvironment.LOCAL, AppEnvironment.TEST])
def test_bootstrap_is_allowed_only_for_local_or_test(environment: AppEnvironment) -> None:
    ensure_bootstrap_allowed(environment)


@pytest.mark.parametrize("environment", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION])
def test_bootstrap_rejects_non_local_environments(environment: AppEnvironment) -> None:
    with pytest.raises(BootstrapNotAllowed):
        ensure_bootstrap_allowed(environment)


def test_bootstrap_normalizes_email_and_restricts_slug() -> None:
    assert normalize_email("  Developer@Example.Test ") == "developer@example.test"
    assert normalize_slug(" Local-Interview_01 ") == "local-interview_01"

    with pytest.raises(ValueError):
        normalize_email("not-an-email")
    with pytest.raises(ValueError):
        normalize_slug("unsafe tenant/path")
