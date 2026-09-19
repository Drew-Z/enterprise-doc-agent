"""Repeatable, isolated Keycloak + SMTP/API validation; never sends Internet email."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import smtplib
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psutil
from pydantic import SecretStr

from enterprise_doc_api.browser_auth.oidc import OidcClient, OidcFailure
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_core.config import AppEnvironment

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/identity/lab.compose.yaml"
SENDER = "noreply@notify.ciallobill.ccwu.cc"
OWNER = "owner01@mailtest.ciallobill.ccwu.cc"
MEMBER = "member01@mailtest.ciallobill.ccwu.cc"
REALM = "docagent-local-check"


class LabFailure(RuntimeError):
    """A fixed, locally authored diagnostic that contains no provider payload."""


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        body = (
            b"<!doctype html><title>Local identity check</title>"
            b"<p>Authorization returned to the local test client.</p>"
        )
        self.send_response(200 if self.path.split("?", 1)[0] == "/auth/callback" else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


def _require(condition: object, label: str) -> None:
    if not condition:
        raise LabFailure(label)


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run(args: list[str], *, env: dict[str, str], timeout: int = 120) -> str:
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=timeout
    )
    _require(result.returncode == 0, "Local infrastructure command failed")
    return result.stdout


def _wait_http(client: httpx.Client, url: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Local identity service did not become ready")


def _admin_token(client: httpx.Client, base: str, env: dict[str, str]) -> str:
    response = client.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": env["LAB_ADMIN_USERNAME"],
            "password": env["LAB_ADMIN_PASSWORD"],
        },
    )
    _require(response.status_code == 200, "Local bootstrap login failed")
    return str(response.json()["access_token"])


def realm_configuration(web_origin: str, env: dict[str, str]) -> dict[str, Any]:
    return {
        "realm": REALM,
        "displayName": "DocAgent local identity check",
        "enabled": True,
        "sslRequired": "none",
        "registrationAllowed": False,
        "resetPasswordAllowed": True,
        "verifyEmail": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "internationalizationEnabled": False,
        "defaultSignatureAlgorithm": "RS256",
        "smtpServer": {
            "host": "relay",
            "port": "8025",
            "from": SENDER,
            "fromDisplayName": "DocAgent",
            "auth": "true",
            "user": SENDER,
            "password": env["LAB_SMTP_PASSWORD"],
            "ssl": "false",
            "starttls": "false",
        },
        "clients": [
            {
                "clientId": "docagent-local",
                "protocol": "openid-connect",
                "enabled": True,
                "publicClient": False,
                "clientAuthenticatorType": "client-secret",
                "secret": env["LAB_CLIENT_SECRET"],
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "fullScopeAllowed": False,
                "redirectUris": [web_origin + "/auth/callback"],
                "webOrigins": [web_origin],
                "defaultClientScopes": ["email"],
                "optionalClientScopes": [],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "id.token.signed.response.alg": "RS256",
                    "use.refresh.tokens": "false",
                },
            }
        ],
        "users": [
            {
                "username": OWNER,
                "email": OWNER,
                "firstName": "Synthetic",
                "lastName": "Owner",
                "enabled": True,
                "emailVerified": False,
                "credentials": [
                    {"type": "password", "value": env["LAB_USER_PASSWORD"], "temporary": False}
                ],
            }
        ],
    }


def _smtp_rejections(port: int, password: str) -> list[str]:
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Local SMTP relay did not become ready") from None
            time.sleep(0.25)
    checks = []
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = SENDER, OWNER, "Denied test"
    message.set_content("Synthetic rejection check")
    with smtplib.SMTP("127.0.0.1", port, timeout=10) as smtp:
        smtp.ehlo()
        _require(smtp.mail(SENDER)[0] == 530, "SMTP accepted anonymous MAIL")
        checks.append("anonymous_smtp_rejected")
        try:
            smtp.login(SENDER, "incorrect-password")
        except smtplib.SMTPAuthenticationError as error:
            _require(error.smtp_code == 535, "Wrong SMTP rejection code")
        else:
            raise RuntimeError("SMTP accepted an incorrect password")
        checks.append("wrong_smtp_password_rejected")
        smtp.login(SENDER, password)
        _require(smtp.mail(SENDER)[0] == 250, "SMTP rejected configured sender")
        _require(smtp.rcpt("unapproved@example.com")[0] == 550, "SMTP accepted unknown recipient")
        checks.append("unapproved_recipient_rejected")
        _require(smtp.rcpt(OWNER)[0] == 250, "SMTP rejected configured recipient")
        _require(smtp.rcpt(MEMBER)[0] == 550, "SMTP accepted a second recipient")
        checks.append("multiple_recipients_rejected")
        smtp.rset()
        try:
            smtp.sendmail("wrong@example.com", [OWNER], message.as_bytes())
        except smtplib.SMTPDataError as error:
            _require(error.smtp_code == 550, "Wrong sender rejection code")
        else:
            raise RuntimeError("SMTP accepted an unconfigured sender")
        checks.append("wrong_sender_rejected")
    return checks


def _browser(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    process = subprocess.Popen(
        ["node", str(ROOT / "tests/identity_provider/keycloak_browser.mjs")],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        output, _ = process.communicate(json.dumps(payload), timeout=120)
    except subprocess.TimeoutExpired:
        # Only terminate descendants of the process this call just created.
        for child in psutil.Process(process.pid).children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.communicate()
        raise RuntimeError("Local browser check timed out") from None
    result: dict[str, Any] = json.loads(output)
    _require(
        result.get("ok") and process.returncode == 0,
        "Browser check failed during "
        + str(result.get("stage", "startup"))
        + " ("
        + str(result.get("networkCode") or result.get("errorType", "unknown"))
        + ")",
    )
    return result


async def _exchange(
    oidc: OidcClient, result: dict[str, Any], verifier: str, nonce: str, started_at: datetime
) -> None:
    identity = await oidc.exchange(
        code=SecretStr(result["code"]),
        verifier=SecretStr(verifier),
        nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),
        started_at=started_at,
    )
    _require(identity.email == OWNER and identity.email_verified, "Unexpected verified identity")
    try:
        await oidc.exchange(
            code=SecretStr(result["code"]),
            verifier=SecretStr(verifier),
            nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),
            started_at=started_at,
        )
    except OidcFailure:
        return
    raise RuntimeError("Keycloak authorization code could be replayed")


def run_validation(evidence_dir: Path) -> dict[str, Any]:
    evidence_dir = evidence_dir.resolve()
    _require(not (evidence_dir / "report.json").exists(), "Evidence report already exists")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    project = "docagent-identity-" + uuid4().hex[:12]
    env = dict(os.environ)
    for name in (
        "ADMIN_PASSWORD",
        "SITE_PASSWORD",
        "ADDRESS_TOKEN",
        "SMTP_PASSWORD",
        "CAPTURE_KEY",
        "CLIENT_SECRET",
        "USER_PASSWORD",
        "NEW_PASSWORD",
    ):
        env["LAB_" + name] = secrets.token_urlsafe(32)
    env["LAB_ADMIN_USERNAME"] = "lab-" + secrets.token_hex(8)
    ports = {_port() for _ in range(4)}
    _require(len(ports) == 4, "Could not allocate distinct local ports")
    keycloak_port, smtp_port, capture_port, callback_port = sorted(ports)
    env.update(
        {
            "LAB_KEYCLOAK_PORT": str(keycloak_port),
            "LAB_SMTP_PORT": str(smtp_port),
            "LAB_CAPTURE_PORT": str(capture_port),
            "LAB_RECIPIENTS": json.dumps([OWNER, MEMBER]),
        }
    )
    keycloak = f"http://127.0.0.1:{keycloak_port}"
    capture = f"http://127.0.0.1:{capture_port}"
    web_origin = f"http://127.0.0.1:{callback_port}"
    compose = [
        "docker",
        "compose",
        "--ansi",
        "never",
        "--project-name",
        project,
        "--file",
        str(COMPOSE),
    ]
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "project": project,
        "status": "running",
        "keycloak_real": True,
        "mail_boundary": "local_http_capture",
        "cloudflare_delivery_verified": False,
        "application_oidc_client": "enterprise_doc_api.browser_auth.oidc.OidcClient",
        "application_http_session_verified": False,
        "ports": [keycloak_port, smtp_port, capture_port, callback_port],
        "checks": [],
        "containers_and_networks_removed": False,
        "callback_listener_closed": False,
        "resources_removed": False,
    }
    stage = "compose_validation"
    attempted_up = False
    callback_server: HTTPServer | None = None
    callback_thread: threading.Thread | None = None
    try:
        callback_server = HTTPServer(("127.0.0.1", callback_port), CallbackHandler)
        callback_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
        callback_thread.start()
        _run([*compose, "config", "--quiet"], env=env)
        _require(
            not _run(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
                env=env,
            ).strip(),
            "Generated project already exists",
        )
        stage = "start_containers"
        print("identity-lab: starting isolated Keycloak, SMTP and capture services", flush=True)
        attempted_up = True
        _run([*compose, "up", "--detach", "--pull", "never"], env=env)
        report["container_ids"] = _run([*compose, "ps", "--all", "--quiet"], env=env).splitlines()
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            stage = "wait_services"
            _wait_http(client, keycloak + "/realms/master/.well-known/openid-configuration")
            _wait_http(client, capture + "/health")
            stage = "smtp_rejections"
            report["checks"].extend(_smtp_rejections(smtp_port, env["LAB_SMTP_PASSWORD"]))
            headers = {"Authorization": "Bearer " + _admin_token(client, keycloak, env)}
            stage = "configure_realm"
            info = client.get(keycloak + "/admin/serverinfo", headers=headers)
            _require(info.status_code == 200, "Keycloak version could not be read")
            report["keycloak_version"] = info.json()["systemInfo"]["version"]
            response = client.post(
                keycloak + "/admin/realms",
                headers=headers,
                json=realm_configuration(web_origin, env),
            )
            _require(response.status_code == 201, "Local realm creation failed")
            issuer = keycloak + "/realms/" + REALM
            settings = BrowserAuthSettings(
                enabled=True,
                web_origin=web_origin,
                issuer=issuer,
                authorization_endpoint=issuer + "/protocol/openid-connect/auth",
                token_endpoint=issuer + "/protocol/openid-connect/token",
                jwks_url=issuer + "/protocol/openid-connect/certs",
                client_id="docagent-local",
                client_secret=SecretStr(env["LAB_CLIENT_SECRET"]),
                algorithms=("RS256",),
            )
            settings.validate_environment(AppEnvironment.TEST)
            oidc = OidcClient(settings)
            report["checks"].append("existing_browser_auth_settings_accepted")
            capture_headers = {"x-capture-key": env["LAB_CAPTURE_KEY"]}
            for action in ("verify", "reset", "login_after_reset"):
                stage = action
                print("identity-lab: real Keycloak browser action " + action, flush=True)
                messages = client.get(capture + "/messages", headers=capture_headers).json()[
                    "messages"
                ]
                state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
                started_at = datetime.now(UTC)
                authorization_url = oidc.authorization_url(
                    state=SecretStr(state), nonce=SecretStr(nonce), verifier=SecretStr(verifier)
                )
                result = _browser(
                    {
                        "action": action,
                        "authorizationUrl": authorization_url,
                        "keycloakOrigin": keycloak,
                        "webOrigin": web_origin,
                        "captureOrigin": capture,
                        "captureKey": env["LAB_CAPTURE_KEY"],
                        "messageOffset": len(messages),
                        "email": OWNER,
                        "password": env["LAB_USER_PASSWORD"],
                        "newPassword": env["LAB_NEW_PASSWORD"],
                        "evidenceDir": str(evidence_dir),
                    },
                    env,
                )
                _require(result["state"] == state, "Returned authorization state did not match")
                if action in {"verify", "reset"}:
                    _require(result["replayDidNotAuthorize"], "Action link authorized twice")
                    report["checks"].append(action + "_link_replay_rejected")
                else:
                    _require(result["oldPasswordRejected"], "Old password still worked")
                    report["checks"].append("old_password_rejected")
                stage = action + "_oidc_exchange"
                asyncio.run(_exchange(oidc, result, verifier, nonce, started_at))
                report["checks"].extend(
                    [action + "_oidc_verified", action + "_code_replay_rejected"]
                )
            headers = {"Authorization": "Bearer " + _admin_token(client, keycloak, env)}
            users = client.get(
                keycloak + "/admin/realms/" + REALM + "/users", headers=headers
            ).json()
            _require(
                len(users) == 1 and users[0]["emailVerified"] is True,
                "User was not actually email-verified",
            )
            report["checks"].append("keycloak_persisted_email_verified_true")
            messages = client.get(capture + "/messages", headers=capture_headers).json()["messages"]
            report["captured_email_count"] = len(messages)
            _require(len(messages) == 2, "Unexpected number of identity emails")
    except BaseException as error:
        report.update(status="failed", failed_stage=stage, error_type=type(error).__name__)
        if isinstance(error, LabFailure):
            report["diagnostic"] = str(error)
        raise
    finally:
        print("identity-lab: removing this run's containers and network", flush=True)
        cleanup_error: BaseException | None = None
        try:
            if attempted_up:
                _run([*compose, "down", "--timeout", "5"], env=env)
            remaining_containers = _run(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
                env=env,
            ).strip()
            remaining_networks = _run(
                [
                    "docker",
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                ],
                env=env,
            ).strip()
            report["containers_and_networks_removed"] = (
                not remaining_containers and not remaining_networks
            )
            _require(report["containers_and_networks_removed"], "Local identity resources remain")
        except BaseException as error:
            cleanup_error = error
        try:
            if callback_server is not None:
                try:
                    callback_server.shutdown()
                finally:
                    callback_server.server_close()
            if callback_thread is not None:
                callback_thread.join(timeout=5)
                _require(not callback_thread.is_alive(), "Local callback listener remains")
            report["callback_listener_closed"] = True
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
        report["resources_removed"] = (
            report["containers_and_networks_removed"] and report["callback_listener_closed"]
        )
        if cleanup_error is not None:
            report["status"] = "failed"
            report.setdefault("failed_stage", "cleanup")
            report["cleanup_error_type"] = type(cleanup_error).__name__
            if isinstance(cleanup_error, LabFailure):
                report["cleanup_diagnostic"] = str(cleanup_error)
        elif report["status"] == "running":
            report["status"] = "passed"
        report["finished_at"] = datetime.now(UTC).isoformat()
        (evidence_dir / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if cleanup_error is not None:
            raise cleanup_error
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_validation(args.evidence_dir)
    except Exception:
        print("identity-lab: validation failed; inspect the sanitized report.json")
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(report["checks"]),
                "resources_removed": report["resources_removed"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
