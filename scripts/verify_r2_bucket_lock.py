from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

API_BASE_URL = "https://api.cloudflare.com/client/v4"
ACCOUNT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
SNAPSHOT_PREFIX_PATTERN = re.compile(
    r"^enterprise-doc-recovery/snapshots/[a-z0-9][a-z0-9-]{0,62}/$"
)


class BucketLockValidationError(ValueError):
    """Raised when a reviewed R2 Bucket Lock prerequisite is not satisfied."""


def _parse_datetime(value: str, *, field: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise BucketLockValidationError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise BucketLockValidationError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _rules(payload: Any, *, bucket: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise BucketLockValidationError(
            f"Cloudflare returned an unsuccessful response for {bucket}"
        )
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("rules"), list):
        raise BucketLockValidationError(f"Cloudflare returned malformed lock rules for {bucket}")
    return [rule for rule in result["rules"] if isinstance(rule, dict)]


def _retention_end(
    condition: Any,
    *,
    checked_at: datetime,
    bucket: str,
) -> tuple[str, datetime | None]:
    if not isinstance(condition, dict):
        raise BucketLockValidationError(f"Bucket Lock condition is malformed for {bucket}")
    condition_type = condition.get("type")
    if condition_type == "Indefinite":
        return "Indefinite", None
    if condition_type == "Date":
        date_value = condition.get("date")
        if not isinstance(date_value, str):
            raise BucketLockValidationError(f"Bucket Lock date is malformed for {bucket}")
        return "Date", _parse_datetime(date_value, field=f"{bucket} retention date")
    if condition_type == "Age":
        max_age = condition.get("maxAgeSeconds")
        if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
            raise BucketLockValidationError(f"Bucket Lock maxAgeSeconds is malformed for {bucket}")
        return "Age", checked_at + timedelta(seconds=max_age)
    raise BucketLockValidationError(f"Bucket Lock condition type is unsupported for {bucket}")


def verify_bucket_locks(
    *,
    account_id: str,
    buckets: list[str],
    prefix: str,
    rule_id: str,
    minimum_retention_until: datetime,
    token: str,
    client: httpx.Client | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise BucketLockValidationError("account_id must be a 32-character lowercase hex value")
    if not buckets or len(set(buckets)) != len(buckets):
        raise BucketLockValidationError("buckets must be a non-empty unique list")
    if any(not BUCKET_PATTERN.fullmatch(bucket) for bucket in buckets):
        raise BucketLockValidationError("bucket names must use the R2 lowercase naming format")
    if not SNAPSHOT_PREFIX_PATTERN.fullmatch(prefix):
        raise BucketLockValidationError(
            "prefix must be an enterprise-doc-recovery/snapshots/<drill-id>/ path"
        )
    if not rule_id.strip():
        raise BucketLockValidationError("rule_id must not be empty")
    if not token:
        raise BucketLockValidationError("Cloudflare API token is missing")
    if minimum_retention_until.tzinfo is None:
        raise BucketLockValidationError("minimum_retention_until must include a timezone")

    observed_at = (checked_at or datetime.now(UTC)).astimezone(UTC)
    minimum_retention = minimum_retention_until.astimezone(UTC)
    if minimum_retention <= observed_at:
        raise BucketLockValidationError("minimum_retention_until must be in the future")

    owned_client = client is None
    active_client = client or httpx.Client(timeout=30.0, follow_redirects=False)
    verified: list[dict[str, Any]] = []
    try:
        for bucket in buckets:
            response = active_client.get(
                f"{API_BASE_URL}/accounts/{account_id}/r2/buckets/{bucket}/lock",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                raise BucketLockValidationError(
                    f"Cloudflare Bucket Lock request failed for {bucket} "
                    f"with HTTP {response.status_code}"
                )
            matching = [
                rule
                for rule in _rules(response.json(), bucket=bucket)
                if rule.get("id") == rule_id and rule.get("prefix") == prefix
            ]
            if len(matching) != 1:
                raise BucketLockValidationError(
                    f"expected exactly one matching Bucket Lock rule for {bucket}"
                )
            rule = matching[0]
            if rule.get("enabled") is not True:
                raise BucketLockValidationError(f"Bucket Lock rule is disabled for {bucket}")
            condition_type, retention_end = _retention_end(
                rule.get("condition"), checked_at=observed_at, bucket=bucket
            )
            if retention_end is not None and retention_end < minimum_retention:
                raise BucketLockValidationError(
                    f"Bucket Lock retention ends before the reviewed minimum for {bucket}"
                )
            verified.append(
                {
                    "bucket": bucket,
                    "rule_id": rule_id,
                    "prefix": prefix,
                    "enabled": True,
                    "condition_type": condition_type,
                    "retention_end": retention_end.isoformat() if retention_end else None,
                }
            )
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        raise BucketLockValidationError(
            "Cloudflare Bucket Lock response could not be read"
        ) from error
    finally:
        if owned_client:
            active_client.close()

    return {
        "schema_version": 1,
        "status": "passed",
        "checked_at": observed_at.isoformat(),
        "account_id": account_id,
        "prefix": prefix,
        "minimum_retention_until": minimum_retention.isoformat(),
        "buckets": verified,
        "token_redacted": True,
    }


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify reviewed Cloudflare R2 Bucket Lock rules without exposing credentials"
    )
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--bucket", action="append", dest="buckets", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--minimum-retention-until", required=True)
    parser.add_argument("--token-env", default="CLOUDFLARE_API_TOKEN")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "")
    try:
        report = verify_bucket_locks(
            account_id=args.account_id,
            buckets=args.buckets,
            prefix=args.prefix,
            rule_id=args.rule_id,
            minimum_retention_until=_parse_datetime(
                args.minimum_retention_until, field="minimum_retention_until"
            ),
            token=token,
        )
        if args.output:
            _write_private_json(args.output, report)
    except BucketLockValidationError as error:
        message = str(error).replace(token, "[redacted]") if token else str(error)
        print(json.dumps({"status": "failed", "error": message}, sort_keys=True), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "passed",
                "bucket_count": len(report["buckets"]),
                "prefix": report["prefix"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
