from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from scripts.staging_smoke import (
        SmokeClient,
        StagingSmokeFailure,
        UrlLibSmokeClient,
        _required_mapping,
        _required_str,
    )
except ModuleNotFoundError:
    from staging_smoke import (  # type: ignore[import-not-found,no-redef]
        SmokeClient,
        StagingSmokeFailure,
        UrlLibSmokeClient,
        _required_mapping,
        _required_str,
    )


class GovernanceSmokeFailure(RuntimeError):
    def __init__(self, step: str, reason: str, *, http_status: int | None = None) -> None:
        super().__init__(reason)
        self.step = step
        self.reason = reason
        self.http_status = http_status


@dataclass(slots=True)
class Step:
    name: str
    status: str = "passed"
    http_status: int | None = None
    count: int | None = None


def _safe_step(step: Step) -> dict[str, object]:
    result: dict[str, object] = {"name": step.name, "status": step.status}
    if step.http_status is not None:
        result["http_status"] = step.http_status
    if step.count is not None:
        result["count"] = max(0, min(step.count, 500))
    return result


def _request(
    client: SmokeClient,
    steps: list[Step],
    *,
    name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected_statuses: set[int] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        value = client.request_json(
            method,
            path,
            payload=payload,
            headers=headers,
            expected_statuses=expected_statuses,
        )
    except (StagingSmokeFailure, OSError, TimeoutError) as error:
        raise GovernanceSmokeFailure(name, f"request_failed:{type(error).__name__}") from error
    steps.append(Step(name=name))
    return value


def _mapping(value: object, *, step: str) -> dict[str, Any]:
    try:
        return _required_mapping(value, step)
    except StagingSmokeFailure as error:
        raise GovernanceSmokeFailure(step, "invalid_response") from error


def _string(value: dict[str, Any], key: str, *, step: str) -> str:
    try:
        return _required_str(value, key)
    except StagingSmokeFailure as error:
        raise GovernanceSmokeFailure(step, "missing_response_field") from error


def _list(value: object, *, step: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise GovernanceSmokeFailure(step, "invalid_response_list")
    return value


def _wait_for(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    sleep: Callable[[float], None],
    step: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        sleep(1.0)
    raise GovernanceSmokeFailure(step, "timeout")


def _inventory_contains(
    client: SmokeClient,
    document_id: str,
    steps: list[Step],
    *,
    request_step: str,
) -> bool:
    payload = _request(
        client,
        steps,
        name=request_step,
        method="GET",
        path="/api/documents?limit=200",
    )
    items = _list(payload, step=request_step)
    return any(item.get("documentId") == document_id for item in items)


def _upload_document(owner: SmokeClient, steps: list[Step]) -> tuple[str, str]:
    content = b"Governance smoke fixture. No customer data."
    digest = hashlib.sha256(content).hexdigest()
    suffix = uuid4().hex
    created = _mapping(
        _request(
            owner,
            steps,
            name="acl.upload_session_created",
            method="POST",
            path="/api/upload-sessions",
            payload={
                "filename": "governance-smoke.txt",
                "sizeBytes": len(content),
                "mediaType": "text/plain",
                "sha256": digest,
            },
            headers={"Idempotency-Key": f"governance-upload-{suffix}"},
            expected_statuses={200, 201},
        ),
        step="acl.upload_session_created",
    )
    session_id = _string(created, "sessionId", step="acl.upload_session_created")
    presign = _mapping(
        _request(
            owner,
            steps,
            name="acl.part_presigned",
            method="POST",
            path=f"/api/upload-sessions/{session_id}/parts/1/presign",
            payload={
                "sizeBytes": len(content),
                "checksumSha256": base64.b64encode(hashlib.sha256(content).digest()).decode(
                    "ascii"
                ),
            },
        ),
        step="acl.part_presigned",
    )
    headers = presign.get("headers")
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise GovernanceSmokeFailure("acl.part_presigned", "invalid_upload_headers")
    try:
        etag = owner.put_bytes(
            _string(presign, "url", step="acl.part_presigned"), content=content, headers=headers
        )
    except (StagingSmokeFailure, OSError, TimeoutError) as error:
        raise GovernanceSmokeFailure("acl.object_uploaded", "object_upload_failed") from error
    steps.append(Step(name="acl.object_uploaded"))
    completed = _mapping(
        _request(
            owner,
            steps,
            name="acl.upload_completed",
            method="POST",
            path=f"/api/upload-sessions/{session_id}/complete",
            payload={
                "parts": [
                    {
                        "partNumber": 1,
                        "sizeBytes": len(content),
                        "etag": etag,
                        "checksumSha256": base64.b64encode(hashlib.sha256(content).digest()).decode(
                            "ascii"
                        ),
                    }
                ]
            },
        ),
        step="acl.upload_completed",
    )
    return (
        _string(completed, "documentId", step="acl.upload_completed"),
        _string(completed, "versionId", step="acl.upload_completed"),
    )


def _run_acl(
    owner: SmokeClient,
    member: SmokeClient,
    steps: list[Step],
    *,
    member_user_id: str,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    document_id, version_id = _upload_document(owner, steps)

    def ready() -> bool:
        payload = _request(
            owner,
            steps,
            name="acl.document_ready_poll",
            method="GET",
            path="/api/agent-runs/ready-document-versions",
        )
        return any(
            item.get("versionId") == version_id
            for item in _list(payload, step="acl.document_ready_poll")
        )

    _wait_for(ready, timeout_seconds=timeout_seconds, sleep=sleep, step="acl.document_ready")
    steps.append(Step(name="acl.document_ready"))
    _request(
        owner,
        steps,
        name="acl.restricted",
        method="PUT",
        path=f"/api/documents/{document_id}/access",
        payload={"accessMode": "restricted"},
    )
    before = _list(
        _request(
            member, steps, name="acl.member_denied", method="GET", path="/api/documents?limit=200"
        ),
        step="acl.member_denied",
    )
    if any(item.get("documentId") == document_id for item in before):
        raise GovernanceSmokeFailure("acl.member_denied", "restricted_document_visible")
    grant = _mapping(
        _request(
            owner,
            steps,
            name="acl.member_granted",
            method="POST",
            path=f"/api/documents/{document_id}/grants",
            payload={"granteeUserId": member_user_id},
            expected_statuses={201},
        ),
        step="acl.member_granted",
    )
    grant_id = _string(grant, "grantId", step="acl.member_granted")
    _wait_for(
        lambda: _inventory_contains(
            member, document_id, steps, request_step="acl.member_inventory_poll"
        ),
        timeout_seconds=timeout_seconds,
        sleep=sleep,
        step="acl.member_visible",
    )
    steps.append(Step(name="acl.member_visible"))
    _request(
        member,
        steps,
        name="acl.member_policy_forbidden",
        method="PUT",
        path=f"/api/documents/{document_id}/access",
        payload={"accessMode": "tenant"},
        expected_statuses={403},
    )
    _request(
        owner,
        steps,
        name="acl.member_revoked",
        method="DELETE",
        path=f"/api/documents/{document_id}/grants/{grant_id}",
        expected_statuses={204},
    )
    _wait_for(
        lambda: (
            not _inventory_contains(
                member, document_id, steps, request_step="acl.member_inventory_poll"
            )
        ),
        timeout_seconds=timeout_seconds,
        sleep=sleep,
        step="acl.member_revocation_visible",
    )
    steps.append(Step(name="acl.member_revocation_visible"))


def _run_audit_governance(
    owner: SmokeClient,
    member: SmokeClient,
    steps: list[Step],
    *,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    original = _mapping(
        _request(
            owner,
            steps,
            name="audit.policy_read",
            method="GET",
            path="/api/audit-governance/retention-policy",
        ),
        step="audit.policy_read",
    )
    original_days = original.get("retentionDays")
    original_enabled = original.get("isEnabled")
    if not isinstance(original_days, int) or not isinstance(original_enabled, bool):
        raise GovernanceSmokeFailure("audit.policy_read", "invalid_policy")
    hold_id: str | None = None
    primary_error: GovernanceSmokeFailure | None = None
    try:
        _request(
            member,
            steps,
            name="audit.member_forbidden",
            method="GET",
            path="/api/audit-governance/legal-holds",
            expected_statuses={403},
        )
        updated_policy = _mapping(
            _request(
                owner,
                steps,
                name="audit.policy_updated",
                method="PUT",
                path="/api/audit-governance/retention-policy",
                payload={"retentionDays": 30, "isEnabled": True},
            ),
            step="audit.policy_updated",
        )
        if updated_policy.get("retentionDays") != 30 or updated_policy.get("isEnabled") is not True:
            raise GovernanceSmokeFailure("audit.policy_updated", "policy_update_not_applied")
        hold = _mapping(
            _request(
                owner,
                steps,
                name="audit.legal_hold_created",
                method="POST",
                path="/api/audit-governance/legal-holds",
                payload={
                    "name": f"Governance smoke {uuid4().hex}",
                    "reason": "Synthetic governance smoke hold",
                },
                expected_statuses={201},
            ),
            step="audit.legal_hold_created",
        )
        hold_id = _string(hold, "holdId", step="audit.legal_hold_created")
        holds = _list(
            _request(
                owner,
                steps,
                name="audit.holds_listed",
                method="GET",
                path="/api/audit-governance/legal-holds",
            ),
            step="audit.holds_listed",
        )
        if not any(item.get("holdId") == hold_id for item in holds):
            raise GovernanceSmokeFailure("audit.holds_listed", "legal_hold_not_listed")
        _request(
            owner,
            steps,
            name="audit.retention_preview",
            method="GET",
            path="/api/audit-governance/retention-preview",
        )
        _request(
            owner,
            steps,
            name="audit.legal_hold_released",
            method="DELETE",
            path=f"/api/audit-governance/legal-holds/{hold_id}",
        )
        hold_id = None
        plan = _mapping(
            _request(
                owner,
                steps,
                name="audit.retention_plan",
                method="GET",
                path="/api/audit-governance/retention-plan?limit=25",
            ),
            step="audit.retention_plan",
        )
        eligible = plan.get("eligibleEventCount")
        if not isinstance(eligible, int) or eligible < 0:
            raise GovernanceSmokeFailure("audit.retention_plan", "invalid_eligible_count")
        if eligible:
            archive = _mapping(
                _request(
                    owner,
                    steps,
                    name="audit.retention_archived",
                    method="POST",
                    path="/api/audit-governance/retention-archive?limit=25",
                    expected_statuses={201},
                ),
                step="audit.retention_archived",
            )
            batch_id = _string(archive, "batchId", step="audit.retention_archived")
            archives = _list(
                _request(
                    owner,
                    steps,
                    name="audit.archives_listed",
                    method="GET",
                    path="/api/audit-governance/retention-archives?limit=10",
                ),
                step="audit.archives_listed",
            )
            if not any(item.get("batchId") == batch_id for item in archives):
                raise GovernanceSmokeFailure("audit.archives_listed", "archive_batch_not_listed")
            verification = _mapping(
                _request(
                    owner,
                    steps,
                    name="audit.archive_verified",
                    method="POST",
                    path=f"/api/audit-governance/retention-archives/{batch_id}/verify",
                ),
                step="audit.archive_verified",
            )
            if verification.get("valid") is not True:
                raise GovernanceSmokeFailure("audit.archive_verified", "archive_not_valid")
        else:
            steps.append(Step(name="audit.archive_skipped_no_eligible_events", count=0))
    except GovernanceSmokeFailure as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[GovernanceSmokeFailure] = []
        if hold_id is not None:
            try:
                _request(
                    owner,
                    steps,
                    name="audit.legal_hold_released",
                    method="DELETE",
                    path=f"/api/audit-governance/legal-holds/{hold_id}",
                )
            except GovernanceSmokeFailure as error:
                cleanup_errors.append(error)
                steps.append(Step(name="audit.cleanup_hold_release", status="failed"))
        try:
            _request(
                owner,
                steps,
                name="audit.policy_restored",
                method="PUT",
                path="/api/audit-governance/retention-policy",
                payload={"retentionDays": original_days, "isEnabled": original_enabled},
            )
        except GovernanceSmokeFailure as error:
            cleanup_errors.append(error)
            steps.append(Step(name="audit.cleanup_policy_restore", status="failed"))
        if primary_error is None and cleanup_errors:
            raise GovernanceSmokeFailure("audit.cleanup", "cleanup_failed") from cleanup_errors[0]


def _run_identity(owner: SmokeClient, steps: list[Step], *, member_user_id: str) -> None:
    members = _list(
        _request(
            owner,
            steps,
            name="identity.members_listed",
            method="GET",
            path="/api/identity-bindings/members",
        ),
        step="identity.members_listed",
    )
    candidates = [
        item
        for item in members
        if item.get("role") == "member" and isinstance(item.get("userId"), str)
    ]
    if not candidates:
        raise GovernanceSmokeFailure("identity.members_listed", "no_active_member")
    user_ids = {str(item["userId"]) for item in candidates}
    if member_user_id not in user_ids:
        raise GovernanceSmokeFailure("identity.members_listed", "member_principal_not_listed")
    user_id = member_user_id
    binding = _mapping(
        _request(
            owner,
            steps,
            name="identity.binding_created",
            method="POST",
            path="/api/identity-bindings",
            payload={
                "issuer": "https://governance-smoke.invalid/issuer",
                "subject": f"governance-smoke-{uuid4().hex}",
                "userId": user_id,
            },
            expected_statuses={201},
        ),
        step="identity.binding_created",
    )
    binding_id = _string(binding, "bindingId", step="identity.binding_created")
    try:
        _request(
            owner,
            steps,
            name="identity.bindings_listed",
            method="GET",
            path="/api/identity-bindings",
        )
        deactivated = _mapping(
            _request(
                owner,
                steps,
                name="identity.binding_deactivated",
                method="DELETE",
                path=f"/api/identity-bindings/{binding_id}",
            ),
            step="identity.binding_deactivated",
        )
        if deactivated.get("isActive") is not False:
            raise GovernanceSmokeFailure("identity.binding_deactivated", "binding_still_active")
        reactivated = _mapping(
            _request(
                owner,
                steps,
                name="identity.binding_reactivated",
                method="POST",
                path=f"/api/identity-bindings/{binding_id}/activate",
            ),
            step="identity.binding_reactivated",
        )
        if reactivated.get("isActive") is not True:
            raise GovernanceSmokeFailure("identity.binding_reactivated", "binding_not_active")
        final_bindings = _list(
            _request(
                owner,
                steps,
                name="identity.bindings_verified",
                method="GET",
                path="/api/identity-bindings",
            ),
            step="identity.bindings_verified",
        )
        if not any(
            item.get("bindingId") == binding_id and item.get("isActive") is True
            for item in final_bindings
        ):
            raise GovernanceSmokeFailure("identity.bindings_verified", "active_binding_not_listed")
    except GovernanceSmokeFailure:
        try:
            _request(
                owner,
                steps,
                name="identity.binding_cleanup_deactivated",
                method="DELETE",
                path=f"/api/identity-bindings/{binding_id}",
                expected_statuses={200, 204},
            )
        except GovernanceSmokeFailure:
            steps.append(Step(name="identity.binding_cleanup_deactivated", status="failed"))
        raise


def run_staging_governance_smoke(
    owner: SmokeClient,
    member: SmokeClient,
    *,
    timeout_seconds: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    started = time.monotonic()
    started_at = datetime.now(UTC)
    steps: list[Step] = []
    try:
        member_session = _mapping(
            _request(
                member,
                steps,
                name="identity.member_session_read",
                method="GET",
                path="/api/session",
            ),
            step="identity.member_session_read",
        )
        member_user_id = _string(member_session, "actorId", step="identity.member_session_read")
        member_list = _list(
            _request(
                owner,
                steps,
                name="identity.members_listed_for_acl",
                method="GET",
                path="/api/identity-bindings/members",
            ),
            step="identity.members_listed_for_acl",
        )
        candidates = [
            item
            for item in member_list
            if item.get("role") == "member" and isinstance(item.get("userId"), str)
        ]
        if not candidates:
            raise GovernanceSmokeFailure("identity.members_listed_for_acl", "no_active_member")
        active_member_ids = {str(item["userId"]) for item in candidates}
        if member_user_id not in active_member_ids:
            raise GovernanceSmokeFailure(
                "identity.members_listed_for_acl", "member_principal_not_active"
            )
        _run_acl(
            owner,
            member,
            steps,
            member_user_id=member_user_id,
            timeout_seconds=timeout_seconds,
            sleep=sleep,
        )
        _run_audit_governance(owner, member, steps, timeout_seconds=timeout_seconds, sleep=sleep)
        _run_identity(owner, steps, member_user_id=member_user_id)
        status = "passed"
        failure: dict[str, object] | None = None
    except GovernanceSmokeFailure as error:
        steps.append(Step(name=error.step, status="failed", http_status=error.http_status))
        status = "failed"
        failure = {"step": error.step, "reason": error.reason}
    completed_at = datetime.now(UTC)
    report: dict[str, Any] = {
        "schema_version": 1,
        "scenario": "staging-governance",
        "status": status,
        "steps": [_safe_step(step) for step in steps],
        "duration_seconds": max(0.0, time.monotonic() - started),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    if failure is not None:
        report["failure"] = failure
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a sanitized staging governance smoke")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allowed-host", action="append", required=True)
    parser.add_argument("--allowed-object-store-host", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    owner_token = os.environ.get("STAGING_GOVERNANCE_OWNER_TOKEN", "")
    member_token = os.environ.get("STAGING_GOVERNANCE_MEMBER_TOKEN", "")
    if not owner_token or not member_token:
        raise SystemExit(
            "STAGING_GOVERNANCE_OWNER_TOKEN and STAGING_GOVERNANCE_MEMBER_TOKEN are required"
        )
    owner = UrlLibSmokeClient(
        base_url=args.base_url,
        token=owner_token,
        allowed_control_plane_hosts=tuple(args.allowed_host),
        allowed_object_store_hosts=tuple(args.allowed_object_store_host),
    )
    member = UrlLibSmokeClient(
        base_url=args.base_url,
        token=member_token,
        allowed_control_plane_hosts=tuple(args.allowed_host),
        allowed_object_store_hosts=tuple(args.allowed_object_store_host),
    )
    report = run_staging_governance_smoke(owner, member, timeout_seconds=args.timeout_seconds)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
