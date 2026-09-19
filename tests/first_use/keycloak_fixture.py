"""Owned real Keycloak/SMTP resources for the product browser journey."""

from __future__ import annotations

import html
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from infra.identity import local_lab as lab
from pydantic import SecretStr

from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_core.config import AppEnvironment
from tests.invitations.browser_server import ACCOUNTS, WEB_ORIGIN


class KeycloakFixture:
    """Credentials and action links never leave memory or private loopback responses."""

    def __init__(self, output: Path) -> None:
        self.output = output.resolve(strict=True)
        if any(
            (self.output / name).exists()
            for name in ("keycloak-context.json", "keycloak-cleanup.json")
        ):
            raise lab.LabFailure("Use a fresh Keycloak evidence directory")
        self.project = "docagent-first-use-" + uuid4().hex[:12]
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "WINDIR",
            "COMSPEC",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "TEMP",
            "TMP",
            "HOMEDRIVE",
            "HOMEPATH",
            "DOCKER_CONFIG",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PROGRAMW6432",
            "COMMONPROGRAMFILES",
            "COMMONPROGRAMFILES(X86)",
        }
        self.env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        for name in (
            "ADMIN_PASSWORD",
            "SITE_PASSWORD",
            "ADDRESS_TOKEN",
            "SMTP_PASSWORD",
            "CAPTURE_KEY",
            "CLIENT_SECRET",
            "USER_PASSWORD",
        ):
            self.env["LAB_" + name] = secrets.token_urlsafe(32)
        self.env["LAB_ADMIN_USERNAME"] = "lab-" + secrets.token_hex(8)
        ports = {lab._port() for _ in range(3)}
        lab._require(len(ports) == 3, "Distinct identity ports were not available")
        keycloak_port, smtp_port, capture_port = sorted(ports)
        self.env.update(
            {
                "LAB_KEYCLOAK_HOST": "localhost",
                "LAB_KEYCLOAK_PORT": str(keycloak_port),
                "LAB_SMTP_PORT": str(smtp_port),
                "LAB_CAPTURE_PORT": str(capture_port),
                "LAB_RECIPIENTS": json.dumps([ACCOUNTS[key][1] for key in ("owner", "desktop")]),
            }
        )
        self.origin = f"http://localhost:{keycloak_port}"
        self.issuer = self.origin + "/realms/" + lab.REALM
        self.capture = f"http://127.0.0.1:{capture_port}"
        self.passwords = {key: secrets.token_urlsafe(32) for key in ("owner", "desktop")}
        self.new_password = secrets.token_urlsafe(32)
        self.subjects: dict[str, str] = {}
        self.compose = [
            "docker",
            "compose",
            "--ansi",
            "never",
            "--project-name",
            self.project,
            "--file",
            str(lab.COMPOSE),
        ]
        self.attempted_up = False
        self.closed = False
        self.version: str | None = None
        self.settings = BrowserAuthSettings(
            enabled=True,
            web_origin=WEB_ORIGIN,
            issuer=self.issuer,
            authorization_endpoint=self.issuer + "/protocol/openid-connect/auth",
            token_endpoint=self.issuer + "/protocol/openid-connect/token",
            jwks_url=self.issuer + "/protocol/openid-connect/certs",
            client_id="docagent-local",
            client_secret=SecretStr(self.env["LAB_CLIENT_SECRET"]),
            algorithms=("RS256",),
        )
        self.settings.validate_environment(AppEnvironment.TEST)

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=10, trust_env=False, follow_redirects=False)

    def _headers(self, client: httpx.Client) -> dict[str, str]:
        return {"Authorization": "Bearer " + lab._admin_token(client, self.origin, self.env)}

    def _get(self, client: httpx.Client, suffix: str, headers: dict[str, str]) -> Any:
        response = client.get(self.origin + "/admin/realms/" + lab.REALM + suffix, headers=headers)
        lab._require(response.status_code == 200, "Local identity state could not be read")
        return response.json()

    def _write(self, name: str, value: object) -> None:
        with (self.output / name).open("x", encoding="utf-8") as target:
            target.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def start(self) -> None:
        lab._run([*self.compose, "config", "--quiet"], env=self.env)
        for resource in ("container", "network"):
            ids = lab._run(
                [
                    "docker",
                    resource,
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + self.project,
                ],
                env=self.env,
            )
            lab._require(not ids.strip(), "Generated identity project already exists")
        self.attempted_up = True
        lab._run([*self.compose, "up", "--detach", "--pull", "never"], env=self.env)
        with self._client() as client:
            lab._wait_http(client, self.origin + "/realms/master/.well-known/openid-configuration")
            lab._wait_http(client, self.capture + "/health")
            headers = self._headers(client)
            info = client.get(self.origin + "/admin/serverinfo", headers=headers)
            lab._require(info.status_code == 200, "Local identity version unavailable")
            self.version = str(info.json()["systemInfo"]["version"])
            lab._require(self.version == "26.7.0", "Unexpected Keycloak version")
            realm = lab.realm_configuration(WEB_ORIGIN, self.env)
            realm.update(
                eventsEnabled=True,
                eventsExpiration=3600,
                eventsListeners=[],
                enabledEventTypes=["LOGIN", "CODE_TO_TOKEN", "VERIFY_EMAIL", "UPDATE_PASSWORD"],
                users=[
                    {
                        "username": ACCOUNTS[key][1],
                        "email": ACCOUNTS[key][1],
                        "firstName": "Synthetic",
                        "lastName": key,
                        "enabled": True,
                        "emailVerified": False,
                        "credentials": [
                            {"type": "password", "value": self.passwords[key], "temporary": False}
                        ],
                    }
                    for key in ("owner", "desktop")
                ],
            )
            response = client.post(self.origin + "/admin/realms", headers=headers, json=realm)
            lab._require(response.status_code == 201, "Product identity realm creation failed")
        state = self.snapshot()
        lab._require(state["verifiedUsers"] == 0, "Users must start without verified email")
        self._write(
            "keycloak-context.json",
            {
                "project": self.project,
                "issuer": self.issuer,
                "version": self.version,
                "identityProvider": "keycloak",
                "mailBoundary": "local_http_capture",
                "cloudflareDeliveryVerified": False,
                "longTermHostingVerified": False,
                "userCount": 2,
                "initialVerifiedUsers": 0,
                "productAndIdentityHostsDiffer": urlsplit(WEB_ORIGIN).hostname
                != urlsplit(self.origin).hostname,
            },
        )

    def messages(self) -> list[dict[str, Any]]:
        with self._client() as client:
            response = client.get(
                self.capture + "/messages", headers={"x-capture-key": self.env["LAB_CAPTURE_KEY"]}
            )
        lab._require(response.status_code == 200, "Local mail capture unavailable")
        values: list[dict[str, Any]] = response.json()["messages"]
        return values

    def browser_context(self, account: str) -> dict[str, object]:
        lab._require(account in self.passwords, "Unknown synthetic account")
        return {
            "origin": self.origin,
            "email": ACCOUNTS[account][1],
            "password": self.passwords[account],
            "newPassword": self.new_password,
            "messageOffset": len(self.messages()),
        }

    def mail_link(self, account: str, offset: int) -> dict[str, object]:
        lab._require(account in self.passwords and 0 <= offset <= 20, "Invalid mail lookup")
        for message in self.messages()[offset:]:
            if message.get("to_mail") != ACCOUNTS[account][1]:
                continue
            for href in re.findall(r'href="([^"]+)"', str(message.get("content", ""))):
                link = html.unescape(href)
                parsed = urlsplit(link)
                if f"{parsed.scheme}://{parsed.netloc}" == self.origin and parsed.path.startswith(
                    "/realms/" + lab.REALM + "/login-actions/"
                ):
                    return {"available": True, "link": link}
            raise lab.LabFailure("Local identity action link missing")
        return {"available": False}

    def snapshot(self) -> dict[str, object]:
        with self._client() as client:
            headers = self._headers(client)
            users = self._get(client, "/users?max=10", headers)
            expected = {ACCOUNTS[key][1] for key in self.passwords}
            lab._require(
                len(users) == 2 and {user["email"] for user in users} == expected,
                "Unexpected identity users",
            )
            self.subjects = {user["email"]: user["id"] for user in users}
            clients = self._get(client, "/clients?clientId=docagent-local", headers)
            lab._require(len(clients) == 1, "Unexpected product identity client")
            settings = clients[0]
            events = self._get(client, "/events?type=CODE_TO_TOKEN&max=100", headers)
            reset_events = self._get(client, "/events?type=UPDATE_PASSWORD&max=100", headers)
        return {
            "version": self.version,
            "issuer": self.issuer,
            "verifiedUsers": sum(user["emailVerified"] is True for user in users),
            "successfulCodeExchanges": sum(event.get("error") is None for event in events),
            "passwordUpdates": sum(event.get("error") is None for event in reset_events),
            "pkceRequired": settings.get("attributes", {}).get("pkce.code.challenge.method")
            == "S256",
            "confidentialClient": settings.get("publicClient") is False,
            "implicitDisabled": settings.get("implicitFlowEnabled") is False,
            "passwordGrantDisabled": settings.get("directAccessGrantsEnabled") is False,
            "exactCallback": settings.get("redirectUris") == [WEB_ORIGIN + "/auth/callback"],
            "capturedEmails": len(self.messages()),
        }

    def close(self) -> None:
        if self.closed:
            return
        failure: BaseException | None = None
        remaining: dict[str, bool] = {}
        try:
            if self.attempted_up:
                lab._run([*self.compose, "down", "--timeout", "5"], env=self.env)
        except BaseException as error:
            failure = error
        for resource in ("container", "network"):
            try:
                result = lab._run(
                    [
                        "docker",
                        resource,
                        "ls",
                        "-aq",
                        "--filter",
                        "label=com.docker.compose.project=" + self.project,
                    ]
                    if resource == "container"
                    else [
                        "docker",
                        resource,
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + self.project,
                    ],
                    env=self.env,
                )
                remaining[resource] = bool(result.strip())
            except BaseException as error:
                remaining[resource] = True
                failure = failure or error
        if any(remaining.values()) and failure is None:
            failure = lab.LabFailure("Owned identity resources remain")
        self._write(
            "keycloak-cleanup.json",
            {
                "project": self.project,
                "finishedAt": datetime.now(UTC).isoformat(),
                "status": "failed" if failure is not None else "passed",
                "containersRemoved": not remaining["container"],
                "networksRemoved": not remaining["network"],
                "resourcesRemoved": not any(remaining.values()),
                "internetEmailsSent": 0,
                "errorType": type(failure).__name__ if failure else None,
            },
        )
        self.closed = True
        if failure is not None:
            raise failure
