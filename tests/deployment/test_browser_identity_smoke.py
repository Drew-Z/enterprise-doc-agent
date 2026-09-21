from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

WEB_ORIGIN = "https://app.example.com"
ISSUER = "https://auth.example.com/realms/docagent"


@pytest.fixture
def http_boundary() -> Iterator[tuple[dict[str, Any], urllib.request.BaseHandler, list[str]]]:
    routes: dict[str, Any] = {}
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            status, content_type, body, headers = routes[self.path]
            raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalTransport(urllib.request.HTTPSHandler):
        def https_open(self, request: urllib.request.Request) -> Any:
            from scripts.browser_identity_smoke import NoRedirect

            mapped = "http://127.0.0.1:" + str(server.server_port) + urlsplit(request.full_url).path
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            return direct.open(mapped, timeout=request.timeout)

    try:
        yield routes, LocalTransport(), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_browser_identity_smoke_rejects_the_live_old_spa_response(http_boundary: Any) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    routes, transport, requests = http_boundary
    routes["/auth/session"] = (200, "text/html", "<html>old application</html>", {})
    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert caught.value.report["status"] == "failed"
    assert caught.value.report["failure"] == {
        "step": "browser_session",
        "code": "unexpected_content_type",
        "http_status": 200,
    }
    assert requests == ["/auth/session"]
    assert "old application" not in json.dumps(caught.value.report)


def _identity_metadata() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
        "token_endpoint": ISSUER + "/protocol/openid-connect/token",
        "jwks_uri": ISSUER + "/protocol/openid-connect/certs",
        "code_challenge_methods_supported": ["S256"],
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def test_browser_entry_accepts_real_http_json_contract_without_retaining_body(
    http_boundary: Any,
) -> None:
    from scripts.browser_identity_smoke import run_smoke

    routes, transport, requests = http_boundary
    routes["/auth/session"] = (200, "application/json", {"status": "anonymous"}, {})
    metadata = _identity_metadata()
    metadata["unused_private_field"] = "must-not-be-recorded"
    routes["/realms/docagent/.well-known/openid-configuration"] = (
        200,
        "application/json",
        metadata,
        {},
    )
    report = run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert report["status"] == "passed"
    assert report["checks"] == ["anonymous_browser_session", "exact_oidc_metadata"]
    assert requests == ["/auth/session", "/realms/docagent/.well-known/openid-configuration"]
    assert "must-not-be-recorded" not in json.dumps(report)


@pytest.mark.parametrize("status", ["disabled", "authenticated"])
def test_browser_entry_requires_anonymous_enabled_session(http_boundary: Any, status: str) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    routes, transport, _ = http_boundary
    routes["/auth/session"] = (200, "application/json", {"status": status}, {})
    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert caught.value.report["failure"]["code"] == "browser_login_unavailable"


@pytest.mark.parametrize("advertised", [["ES256"], ["RS256"]])
def test_hosted_identity_smoke_checks_the_selected_asymmetric_algorithm(
    http_boundary: Any, advertised: list[str]
) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    issuer = "https://project.supabase.co/auth/v1"
    config = {
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "jwks_uri": issuer + "/.well-known/jwks.json",
        "algorithms": ["ES256"],
    }
    routes, transport, requests = http_boundary
    routes["/auth/session"] = (200, "application/json", {"status": "anonymous"}, {})
    metadata = {
        **{key: value for key, value in config.items() if key != "algorithms"},
        "issuer": issuer,
        "code_challenge_methods_supported": ["S256"],
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": advertised,
    }
    routes["/auth/v1/.well-known/openid-configuration"] = (200, "application/json", metadata, {})
    kwargs = {
        "client_id": "configured-client-id",
        "oidc_config": json.dumps(config),
        "opener": urllib.request.build_opener(transport),
    }
    if "ES256" in advertised:
        assert run_smoke(WEB_ORIGIN, issuer, **kwargs)["status"] == "passed"
    else:
        with pytest.raises(BrowserIdentitySmokeFailure) as caught:
            run_smoke(WEB_ORIGIN, issuer, **kwargs)
        assert caught.value.report["failure"]["code"] == "identity_protocol_unsupported"
    assert requests == ["/auth/session", "/auth/v1/.well-known/openid-configuration"]


@pytest.mark.parametrize(
    "body,code",
    [
        ("not-json-private", "invalid_json"),
        ("[]", "invalid_json"),
        ("x" * (128 * 1024 + 1), "response_too_large"),
    ],
    ids=["malformed-json", "not-an-object", "oversized"],
)
def test_browser_entry_bounds_and_redacts_bad_http_responses(
    http_boundary: Any, body: str, code: str
) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    routes, transport, _ = http_boundary
    routes["/auth/session"] = (200, "application/json", body, {})
    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert caught.value.report["failure"]["code"] == code
    if body != "[]":
        assert body not in json.dumps(caught.value.report)


@pytest.mark.parametrize(
    "host,changes,accepted",
    [
        ("team.cloudflareaccess.com", {}, True),
        ("team.cloudflareaccess.com", {"grant_types_supported": ["authorization_code"]}, False),
        ("team.cloudflareaccess.com", {"code_challenge_methods_supported": ["plain"]}, False),
        (
            "team.cloudflareaccess.com",
            {"token_endpoint_auth_methods": ["client_secret_post"]},
            False,
        ),
        ("auth.example.com", {}, False),
    ],
)
def test_cloudflare_metadata_requires_the_explicit_pkce_only_flow(
    http_boundary: Any, host: str, changes: dict[str, Any], accepted: bool
) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    issuer = f"https://{host}/cdn-cgi/access/sso/oidc/client-id"
    config = {
        "authorization_endpoint": issuer + "/authorization",
        "token_endpoint": issuer + "/token",
        "jwks_uri": issuer + "/jwks",
        "algorithms": ["RS256"],
    }
    metadata = {
        **{key: value for key, value in config.items() if key != "algorithms"},
        "issuer": issuer,
        "grant_types_supported": ["authorization_code_with_pkce"],
        "token_endpoint_auth_methods": ["client_secret_basic", "client_secret_post"],
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": ["RS256"],
        **changes,
    }
    routes, transport, _ = http_boundary
    routes["/auth/session"] = (200, "application/json", {"status": "anonymous"}, {})
    routes["/cdn-cgi/access/sso/oidc/client-id/.well-known/openid-configuration"] = (
        200,
        "application/json",
        metadata,
        {},
    )
    kwargs = {
        "client_id": "client-id",
        "oidc_config": json.dumps(config),
        "opener": urllib.request.build_opener(transport),
    }
    if accepted:
        report = run_smoke(WEB_ORIGIN, issuer, **kwargs)
        assert report["status"] == "passed"
        assert report["metadata_notes"] == [
            "cloudflare_pkce_flow_advertised_s256_method_not_advertised"
        ]
    else:
        with pytest.raises(BrowserIdentitySmokeFailure) as caught:
            run_smoke(WEB_ORIGIN, issuer, **kwargs)
        assert caught.value.report["failure"]["code"] == "identity_protocol_unsupported"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("issuer", "https://other.example.com/realms/docagent", "identity_endpoint_mismatch"),
        ("token_endpoint", "https://other.example.com/token", "identity_endpoint_mismatch"),
        ("code_challenge_methods_supported", ["plain"], "identity_protocol_unsupported"),
        (
            "token_endpoint_auth_methods_supported",
            ["client_secret_post"],
            "identity_protocol_unsupported",
        ),
    ],
)
def test_browser_entry_checks_the_identity_service_contract(
    http_boundary: Any, field: str, value: Any, code: str
) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    routes, transport, _ = http_boundary
    routes["/auth/session"] = (200, "application/json", {"status": "anonymous"}, {})
    metadata = _identity_metadata()
    metadata[field] = value
    routes["/realms/docagent/.well-known/openid-configuration"] = (
        200,
        "application/json",
        metadata,
        {},
    )
    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert caught.value.report["checks"] == ["anonymous_browser_session"]
    assert caught.value.report["failure"]["code"] == code


def test_browser_entry_does_not_follow_redirects(http_boundary: Any) -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    routes, transport, requests = http_boundary
    routes["/auth/session"] = (302, "application/json", {}, {"Location": "/unapproved"})
    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(transport))
    assert caught.value.report["failure"]["http_status"] == 302
    assert requests == ["/auth/session"]


def test_browser_entry_redacts_transport_errors() -> None:
    from scripts.browser_identity_smoke import BrowserIdentitySmokeFailure, run_smoke

    class BrokenTransport(urllib.request.HTTPSHandler):
        def https_open(self, request: urllib.request.Request) -> Any:
            raise OSError("do-not-echo-private-proxy-error")

    with pytest.raises(BrowserIdentitySmokeFailure) as caught:
        run_smoke(WEB_ORIGIN, ISSUER, opener=urllib.request.build_opener(BrokenTransport()))
    assert caught.value.report["failure"]["code"] == "transport_error"
    assert "do-not-echo" not in json.dumps(caught.value.report)


def test_browser_entry_cli_persists_redacted_failure_and_exits_nonzero(tmp_path: Path) -> None:
    output = tmp_path / "failure.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "scripts/browser_identity_smoke.py",
            "--web-origin",
            WEB_ORIGIN,
            "--issuer",
            "https://user:never-echo@auth.example.com/realms/docagent",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    report = json.loads(output.read_text())
    assert report["failure"]["code"] == "invalid_configuration"
    assert "never-echo" not in result.stdout + result.stderr + output.read_text()
