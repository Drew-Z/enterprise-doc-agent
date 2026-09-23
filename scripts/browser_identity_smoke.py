"""Check the public anonymous login entry without accounts, mail or model calls."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

try:
    from scripts.configure_staging_manifest import browser_auth_environment
except ModuleNotFoundError:
    from configure_staging_manifest import (  # type: ignore[import-not-found,no-redef]
        browser_auth_environment,
    )

MAX_RESPONSE_BYTES = 128 * 1024


class BrowserIdentitySmokeFailure(ValueError):
    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("browser identity entry check failed")
        self.report = report


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def run_smoke(
    web_origin: str,
    issuer: str,
    *,
    client_id: str | None = None,
    oidc_config: str | None = None,
    provider: str = "oidc",
    timeout_seconds: float = 15,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "checks": [],
        "failure": None,
        "scope": (
            "anonymous_session_and_github_provider_not_authenticated_journey"
            if provider == "github"
            else "anonymous_session_and_oidc_metadata_not_authenticated_journey"
        ),
    }

    def fail(step: str, code: str, status: int | None = None) -> NoReturn:
        report["failure"] = {"step": step, "code": code, "http_status": status}
        raise BrowserIdentitySmokeFailure(report)

    try:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("invalid transport timeout")
        config = browser_auth_environment(
            web_origin, issuer, client_id, oidc_config=oidc_config, provider=provider
        )
        if config["BROWSER_AUTH__ENABLED"] != "true":
            raise ValueError("issuer required")
    except ValueError:
        fail("configuration", "invalid_configuration")
    client = opener or urllib.request.build_opener(NoRedirect())

    def read_json(url: str, step: str) -> dict[str, Any]:
        status: int | None = None
        deadline = time.monotonic() + timeout_seconds
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "docagent-browser-entry/1"},
            )
            with client.open(request, timeout=timeout_seconds) as response:
                status = response.status
                if status != 200:
                    fail(step, "http_error", status)
                if response.headers.get_content_type() != "application/json":
                    fail(step, "unexpected_content_type", status)
                chunks: list[bytes] = []
                size = 0
                while True:
                    if time.monotonic() >= deadline:
                        fail(step, "transport_timeout", status)
                    chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        fail(step, "response_too_large", status)
                value = json.loads(b"".join(chunks))
        except urllib.error.HTTPError as error:
            error.close()
            fail(step, "http_error", error.code)
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            fail(step, "transport_error", status)
        except (ValueError, UnicodeError) as error:
            if isinstance(error, BrowserIdentitySmokeFailure):
                raise
            fail(step, "invalid_json", status)
        if not isinstance(value, dict):
            fail(step, "invalid_json", status)
        return value

    session = read_json(web_origin + "/auth/session", "browser_session")
    expected_session: dict[str, object] = {"status": "anonymous"}
    if provider == "github":
        expected_session["loginProvider"] = "github"
    if session.get("demoAvailable") is True:
        expected_session.update(loginProvider=provider, demoAvailable=True)
    if session != expected_session:
        fail("browser_session", "browser_login_unavailable", 200)
    report["checks"].append("anonymous_browser_session")
    if session.get("demoAvailable") is True:
        report["checks"].append("public_demo_advertised")
    if provider == "github":
        report["checks"].append("github_provider_selected")
        report["status"] = "passed"
        return report
    discovery = read_json(issuer.rstrip("/") + "/.well-known/openid-configuration", "oidc_metadata")
    expected = {
        "issuer": issuer,
        "authorization_endpoint": config["BROWSER_AUTH__AUTHORIZATION_ENDPOINT"],
        "token_endpoint": config["BROWSER_AUTH__TOKEN_ENDPOINT"],
        "jwks_uri": config["BROWSER_AUTH__JWKS_URL"],
    }
    if any(discovery.get(key) != value for key, value in expected.items()):
        fail("oidc_metadata", "identity_endpoint_mismatch", 200)
    pkce = discovery.get("code_challenge_methods_supported")
    if not isinstance(pkce, list) or "S256" not in pkce:
        issuer_parts = urlsplit(issuer)
        cloudflare_pkce_only = (
            "code_challenge_methods_supported" not in discovery
            and (issuer_parts.hostname or "").endswith(".cloudflareaccess.com")
            and client_id is not None
            and issuer_parts.path == "/cdn-cgi/access/sso/oidc/" + client_id
            and discovery.get("grant_types_supported") == ["authorization_code_with_pkce"]
        )
        if not cloudflare_pkce_only:
            fail("oidc_metadata", "identity_protocol_unsupported", 200)
        # Access advertises its PKCE-only flow but omits the standard method field.
        # The application still sends S256; metadata cannot prove a completed login.
        report["metadata_notes"] = ["cloudflare_pkce_flow_advertised_s256_method_not_advertised"]
    for name, required in (
        ("response_types_supported", "code"),
        *(
            ("id_token_signing_alg_values_supported", algorithm)
            for algorithm in json.loads(config["BROWSER_AUTH__ALGORITHMS"])
        ),
    ):
        supported = discovery.get(name)
        if not isinstance(supported, list) or required not in supported:
            fail("oidc_metadata", "identity_protocol_unsupported", 200)
    methods = discovery.get(
        "token_endpoint_auth_methods_supported",
        discovery.get("token_endpoint_auth_methods", ["client_secret_basic"]),
    )
    if not isinstance(methods, list) or "client_secret_basic" not in methods:
        fail("oidc_metadata", "identity_protocol_unsupported", 200)
    report["checks"].append("exact_oidc_metadata")
    report["status"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-origin", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id")
    parser.add_argument("--oidc-config")
    parser.add_argument("--provider", choices=("oidc", "github"), default="oidc")
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_smoke(
            args.web_origin,
            args.issuer,
            client_id=args.client_id,
            oidc_config=args.oidc_config,
            provider=args.provider,
            timeout_seconds=args.timeout_seconds,
        )
    except BrowserIdentitySmokeFailure as error:
        report = error.report
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    try:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except OSError:
        print(json.dumps({"status": "failed", "code": "report_write_failed"}))
        return 1
    print(serialized, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
