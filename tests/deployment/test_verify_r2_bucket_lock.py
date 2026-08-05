from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from scripts import verify_r2_bucket_lock
from scripts.verify_r2_bucket_lock import (
    BucketLockValidationError,
    verify_bucket_locks,
)

ACCOUNT_ID = "2741446a7478f2d8a5ff31df7e077f17"
PREFIX = "enterprise-doc-recovery/snapshots/20260806-staging-r2/"
RULE_ID = "enterprise-doc-20260806-staging-r2"
TOKEN = "super-secret-cloudflare-token"
CHECKED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
MINIMUM_RETENTION = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)


def _client(rules_by_bucket: dict[str, list[dict[str, object]]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        bucket = request.url.path.split("/")[-2]
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json={
                "success": True,
                "errors": [],
                "messages": [],
                "result": {"rules": rules_by_bucket[bucket]},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _rule(condition: dict[str, object], *, enabled: bool = True) -> dict[str, object]:
    return {
        "id": RULE_ID,
        "prefix": PREFIX,
        "enabled": enabled,
        "condition": condition,
    }


def test_verify_bucket_locks_accepts_reviewed_date_and_indefinite_rules() -> None:
    with _client(
        {
            "documents": [_rule({"type": "Date", "date": "2026-08-20T00:00:00Z"})],
            "artifacts": [_rule({"type": "Indefinite"})],
        }
    ) as client:
        report = verify_bucket_locks(
            account_id=ACCOUNT_ID,
            buckets=["documents", "artifacts"],
            prefix=PREFIX,
            rule_id=RULE_ID,
            minimum_retention_until=MINIMUM_RETENTION,
            token=TOKEN,
            client=client,
            checked_at=CHECKED_AT,
        )

    rendered = json.dumps(report)
    assert report["status"] == "passed"
    assert [item["condition_type"] for item in report["buckets"]] == ["Date", "Indefinite"]
    assert TOKEN not in rendered
    assert report["token_redacted"] is True


def test_verify_bucket_locks_accepts_sufficient_age_rule() -> None:
    with _client(
        {
            "documents": [_rule({"type": "Age", "maxAgeSeconds": 14 * 24 * 60 * 60})],
        }
    ) as client:
        report = verify_bucket_locks(
            account_id=ACCOUNT_ID,
            buckets=["documents"],
            prefix=PREFIX,
            rule_id=RULE_ID,
            minimum_retention_until=MINIMUM_RETENTION,
            token=TOKEN,
            client=client,
            checked_at=CHECKED_AT,
        )

    assert report["buckets"][0]["condition_type"] == "Age"


def test_verify_bucket_locks_rejects_short_or_missing_rule() -> None:
    with _client(
        {
            "documents": [_rule({"type": "Date", "date": "2026-08-07T00:00:00Z"})],
            "artifacts": [],
        }
    ) as client:
        with pytest.raises(BucketLockValidationError, match="reviewed minimum"):
            verify_bucket_locks(
                account_id=ACCOUNT_ID,
                buckets=["documents", "artifacts"],
                prefix=PREFIX,
                rule_id=RULE_ID,
                minimum_retention_until=MINIMUM_RETENTION,
                token=TOKEN,
                client=client,
                checked_at=CHECKED_AT,
            )


def test_verify_bucket_locks_rejects_noncanonical_snapshot_prefix() -> None:
    with pytest.raises(BucketLockValidationError, match="prefix must be"):
        verify_bucket_locks(
            account_id=ACCOUNT_ID,
            buckets=["documents"],
            prefix="enterprise-doc-recovery/snapshots/../live/",
            rule_id=RULE_ID,
            minimum_retention_until=MINIMUM_RETENTION,
            token=TOKEN,
            checked_at=CHECKED_AT,
        )


def test_main_reads_token_from_environment_and_keeps_failure_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)

    def fail(**kwargs: object) -> dict[str, object]:
        assert kwargs["token"] == TOKEN
        raise BucketLockValidationError(f"provider rejected {TOKEN}")

    monkeypatch.setattr(verify_r2_bucket_lock, "verify_bucket_locks", fail)
    result = verify_r2_bucket_lock.main(
        [
            "--account-id",
            ACCOUNT_ID,
            "--bucket",
            "documents",
            "--prefix",
            PREFIX,
            "--rule-id",
            RULE_ID,
            "--minimum-retention-until",
            "2026-08-13T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert TOKEN not in captured.err
    assert "[redacted]" in captured.err
