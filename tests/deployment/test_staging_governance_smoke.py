from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


staging_smoke = _load("staging_smoke", ROOT / "scripts" / "staging_smoke.py")
governance_smoke = _load(
    "staging_governance_smoke", ROOT / "scripts" / "staging_governance_smoke.py"
)


class FakeGovernanceClient:
    def __init__(self, *, role: str, state: dict[str, Any]) -> None:
        self.role = role
        self.state = state

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        del headers, expected_statuses
        payload = payload or {}
        if path == "/api/identity-bindings/members":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            return [{"userId": "member-user", "email": "member@example.test", "role": "member"}]
        if path == "/api/session":
            return {"actorId": "member-user", "tenantId": "tenant-1", "role": self.role}
        if path == "/api/upload-sessions":
            return {"sessionId": "session-1"}
        if path.endswith("/parts/1/presign"):
            return {"url": "https://objects.example/upload", "headers": {"x-checksum": "ok"}}
        if path.endswith("/complete"):
            assert payload["parts"][0]["etag"] == '"etag-1"'
            return {"documentId": "document-1", "versionId": "version-1"}
        if path == "/api/agent-runs/ready-document-versions":
            return [{"versionId": "version-1"}]
        if path == "/api/documents?limit=200":
            if self.role == "member":
                return [{"documentId": "document-1"}] if self.state["grant"] else []
            return [{"documentId": "document-1"}]
        if path == "/api/documents/document-1/access":
            if self.role != "owner":
                return {}
            return {
                "documentId": "document-1",
                "accessMode": payload["accessMode"],
                "canManage": True,
            }
        if path == "/api/documents/document-1/grants":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            self.state["grant"] = True
            return {
                "grantId": "grant-1",
                "documentId": "document-1",
                "granteeUserId": "member-user",
            }
        if path == "/api/documents/document-1/grants/grant-1":
            self.state["grant"] = False
            return {}
        if path == "/api/audit-governance/retention-policy":
            if method == "GET":
                return {
                    "retentionDays": self.state["retention_days"],
                    "isEnabled": self.state["retention_enabled"],
                }
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            self.state["retention_days"] = payload["retentionDays"]
            self.state["retention_enabled"] = payload["isEnabled"]
            return {"retentionDays": payload["retentionDays"], "isEnabled": payload["isEnabled"]}
        if path == "/api/audit-governance/legal-holds":
            if self.role != "owner":
                return []
            if method == "GET":
                return [{"holdId": "hold-1"}] if self.state["hold"] else []
            self.state["hold"] = True
            return {"holdId": "hold-1"}
        if path == "/api/audit-governance/legal-holds/hold-1":
            self.state["hold"] = False
            return {"holdId": "hold-1"}
        if path == "/api/audit-governance/retention-preview":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            return {"eligibleEventCount": 0, "protectedEventCount": 1}
        if path.startswith("/api/audit-governance/retention-plan"):
            eligible = 1 if self.state.get("eligible_events") else 0
            return {"eligibleEventCount": eligible, "protectedEventCount": 1}
        if path == "/api/audit-governance/retention-archive?limit=25":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            self.state["archive_created"] = True
            return {"batchId": "batch-1", "eventCount": 1}
        if path == "/api/audit-governance/retention-archives?limit=10":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            return [{"batchId": "batch-1"}] if self.state.get("archive_created") else []
        if path == "/api/audit-governance/retention-archives/batch-1/verify":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            return {"valid": True}
        if path == "/api/identity-bindings":
            if self.role != "owner":
                raise staging_smoke.StagingSmokeFailure("HTTP 403")
            if method == "GET":
                return (
                    [{"bindingId": "binding-1", "isActive": self.state["binding_active"]}]
                    if self.state["binding_created"]
                    else []
                )
            self.state["binding_created"] = True
            self.state["binding_active"] = True
            return {"bindingId": "binding-1", "isActive": True}
        if path == "/api/identity-bindings/binding-1":
            self.state["binding_active"] = False
            return {"bindingId": "binding-1", "isActive": False}
        if path == "/api/identity-bindings/binding-1/activate":
            self.state["binding_active"] = True
            return {"bindingId": "binding-1", "isActive": True}
        raise AssertionError((method, path))

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str:
        assert (
            url == "https://objects.example/upload" and content and headers == {"x-checksum": "ok"}
        )
        return '"etag-1"'

    def get_bytes(self, url: str) -> bytes:
        raise AssertionError(url)


def _clients() -> tuple[FakeGovernanceClient, FakeGovernanceClient]:
    state: dict[str, Any] = {
        "grant": False,
        "hold": False,
        "retention_days": 365,
        "retention_enabled": False,
        "binding_created": False,
        "binding_active": False,
        "eligible_events": False,
        "archive_created": False,
    }
    return FakeGovernanceClient(role="owner", state=state), FakeGovernanceClient(
        role="member", state=state
    )


def test_governance_smoke_covers_acl_audit_and_identity_without_identifiers() -> None:
    owner, member = _clients()
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "passed"
    names = {step["name"] for step in report["steps"]}
    assert {
        "acl.member_visible",
        "acl.member_revocation_visible",
        "audit.archive_skipped_no_eligible_events",
        "identity.binding_reactivated",
    } <= names
    rendered = json.dumps(report, sort_keys=True)
    for secret in ("document-1", "grant-1", "hold-1", "binding-1", "member-user"):
        assert secret not in rendered


def test_governance_smoke_covers_archive_and_verification_branch() -> None:
    owner, member = _clients()
    owner.state["eligible_events"] = True
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "passed"
    names = {step["name"] for step in report["steps"]}
    assert {"audit.retention_archived", "audit.archives_listed", "audit.archive_verified"} <= names


def test_governance_smoke_fails_when_no_active_member_exists() -> None:
    owner, member = _clients()
    owner.request_json = lambda *args, **kwargs: []  # type: ignore[method-assign]
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "failed"
    assert report["failure"] == {
        "reason": "no_active_member",
        "step": "identity.members_listed_for_acl",
    }


def test_governance_smoke_fails_when_member_actor_is_not_active() -> None:
    owner, member = _clients()
    original = member.request_json

    def request_json(method: str, path: str, **kwargs: Any) -> Any:
        if path == "/api/session":
            return {"actorId": "different-member"}
        return original(method, path, **kwargs)

    member.request_json = request_json  # type: ignore[method-assign]
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "failed"
    assert report["failure"] == {
        "reason": "member_principal_not_active",
        "step": "identity.members_listed_for_acl",
    }


def test_governance_smoke_preserves_primary_failure_when_cleanup_also_fails() -> None:
    owner, member = _clients()
    original = owner.request_json

    def request_json(method: str, path: str, **kwargs: Any) -> Any:
        if path == "/api/audit-governance/retention-preview":
            raise governance_smoke.StagingSmokeFailure("preview failed")
        if path == "/api/audit-governance/legal-holds/hold-1" and method == "DELETE":
            raise governance_smoke.StagingSmokeFailure("release failed")
        return original(method, path, **kwargs)

    owner.request_json = request_json  # type: ignore[method-assign]
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "failed"
    assert report["failure"] == {
        "reason": "request_failed:StagingSmokeFailure",
        "step": "audit.retention_preview",
    }
    assert any(
        step["name"] == "audit.cleanup_hold_release" and step["status"] == "failed"
        for step in report["steps"]
    )


def test_governance_smoke_fails_when_reactivated_binding_is_missing_from_final_list() -> None:
    owner, member = _clients()
    original = owner.request_json

    def request_json(method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/activate"):
            result = original(method, path, **kwargs)
            owner.state["hide_final_binding"] = True
            return result
        if (
            path == "/api/identity-bindings"
            and method == "GET"
            and owner.state.get("hide_final_binding", False)
        ):
            return []
        return original(method, path, **kwargs)

    owner.request_json = request_json  # type: ignore[method-assign]
    report = governance_smoke.run_staging_governance_smoke(
        owner, member, timeout_seconds=1, sleep=lambda _: None
    )
    assert report["status"] == "failed"
    assert report["failure"] == {
        "reason": "active_binding_not_listed",
        "step": "identity.bindings_verified",
    }
    assert owner.state["binding_active"] is False


def test_governance_cli_requires_environment_tokens_and_never_has_token_argument() -> None:
    source = (ROOT / "scripts" / "staging_governance_smoke.py").read_text(encoding="utf-8")
    assert 'add_argument("--owner-token"' not in source
    assert 'add_argument("--member-token"' not in source
    assert 'os.environ.get("STAGING_GOVERNANCE_OWNER_TOKEN"' in source
    assert 'os.environ.get("STAGING_GOVERNANCE_MEMBER_TOKEN"' in source


def test_governance_client_reuses_https_allowlist_boundary() -> None:
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        staging_smoke.UrlLibSmokeClient(
            base_url="http://staging.example",
            token="redacted",
            allowed_control_plane_hosts=("staging.example",),
            allowed_object_store_hosts=("objects.example",),
        )
