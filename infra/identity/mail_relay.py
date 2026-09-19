"""Private SMTP adapter for cloudflare_temp_email v1.12.0's external mail API."""

from __future__ import annotations

import asyncio
import hmac
import importlib
import json
import logging
import os
import re
import signal
import threading
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_MESSAGE_BYTES = 262144
MAX_RESPONSE_BYTES = 4096
_ADDRESS = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}\Z")


@dataclass(frozen=True)
class RelayConfig:
    worker_origin: str
    sender: str
    recipients: tuple[str, ...]
    site_password: str = field(repr=False)
    address_token: str = field(repr=False)
    smtp_password: str = field(repr=False)
    test_capture: bool = False

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.worker_origin)
            local_capture = self.test_capture and self.worker_origin == "http://capture:8080"
            valid = (
                (parsed.scheme == "https" or local_capture)
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
                and parsed.port in {None, 443, 8080}
                and not any(ord(c) <= 32 or c == "\\" for c in self.worker_origin)
                and _ADDRESS.fullmatch(self.sender)
                and self.recipients
                and all(_ADDRESS.fullmatch(address) for address in self.recipients)
                and all(
                    len(secret) >= 32 and secret.isascii() and not any(c.isspace() for c in secret)
                    for secret in (self.site_password, self.address_token, self.smtp_password)
                )
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Invalid mail relay configuration")


class MailRelay:
    def __init__(
        self, config: RelayConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport

    async def submit(self, raw: bytes, sender: str, recipients: list[str]) -> str:
        if (
            sender != self.config.sender
            or len(recipients) != 1
            or recipients[0] not in self.config.recipients
        ):
            return "550 Envelope not allowed"
        if len(raw) > MAX_MESSAGE_BYTES:
            return "552 Message too large"
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw)
            if (
                len(message.get_all("From", [])) != 1
                or parseaddr(str(message["From"]))[1] != self.config.sender
                or len(message.get_all("Subject", [])) != 1
                or message.defects
                or any(part.get_content_disposition() == "attachment" for part in message.walk())
            ):
                return "554 Invalid identity email"
            subject = str(message["Subject"])
            body = message.get_body(preferencelist=("html", "plain"))
            content = body.get_content() if body is not None else None
            if (
                not subject
                or len(subject) > 998
                or "\r" in subject
                or "\n" in subject
                or not isinstance(content, str)
                or not content.strip()
                or body is None
            ):
                return "554 Invalid identity email"
            is_html = body.get_content_type() == "text/html"
            from_name = parseaddr(str(message["From"]))[0]
        except (ValueError, LookupError, TypeError):
            return "554 Invalid identity email"
        payload = {
            "token": self.config.address_token,
            "from_name": from_name,
            "to_mail": recipients[0],
            "to_name": "",
            "subject": subject,
            "content": content,
            "is_html": is_html,
        }
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=10,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        self.config.worker_origin + "/external/api/send_mail",
                        headers={"x-custom-auth": self.config.site_password},
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            return "451 Mail service unavailable"
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_RESPONSE_BYTES:
                                return "451 Mail service unavailable"
                        if json.loads(data) != {"status": "ok"}:
                            return "451 Mail service unavailable"
        except (httpx.HTTPError, TimeoutError, ValueError):
            return "451 Mail service unavailable"
        return "250 Message accepted"


def config_from_environment() -> RelayConfig:
    recipients = json.loads(os.environ["MAIL_RELAY_RECIPIENTS"])
    if not isinstance(recipients, list) or any(not isinstance(item, str) for item in recipients):
        raise ValueError("Invalid mail relay configuration")
    return RelayConfig(
        worker_origin=os.environ["MAIL_RELAY_WORKER_ORIGIN"],
        sender=os.environ["MAIL_RELAY_SENDER"],
        recipients=tuple(recipients),
        site_password=os.environ["MAIL_RELAY_SITE_PASSWORD"],
        address_token=os.environ["MAIL_RELAY_ADDRESS_TOKEN"],
        smtp_password=os.environ["MAIL_RELAY_SMTP_PASSWORD"],
        test_capture=os.environ.get("MAIL_RELAY_TEST_CAPTURE") == "true",
    )


def serve() -> None:
    # SMTP libraries may log AUTH data or message bodies. Emit no library logs.
    logging.disable(logging.CRITICAL)
    config = config_from_environment()
    relay = MailRelay(config)
    smtp = importlib.import_module("aiosmtpd.smtp")
    controller_module = importlib.import_module("aiosmtpd.controller")

    class Handler:
        def authenticator(
            self, server: Any, session: Any, envelope: Any, mechanism: str, auth_data: Any
        ) -> Any:
            authenticated = (
                mechanism in {"LOGIN", "PLAIN"}
                and isinstance(auth_data, smtp.LoginPassword)
                and hmac.compare_digest(auth_data.login, config.sender.encode("ascii"))
                and hmac.compare_digest(auth_data.password, config.smtp_password.encode("ascii"))
            )
            return smtp.AuthResult(success=authenticated, handled=authenticated)

        async def handle_RCPT(
            self, server: Any, session: Any, envelope: Any, address: str, options: list[str]
        ) -> str:
            if address not in config.recipients or envelope.rcpt_tos:
                return "550 Recipient not allowed"
            envelope.rcpt_tos.append(address)
            envelope.rcpt_options.extend(options)
            return "250 Recipient accepted"

        async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
            return await relay.submit(envelope.content, envelope.mail_from, envelope.rcpt_tos)

    handler = Handler()
    controller = controller_module.Controller(
        handler,
        hostname="0.0.0.0",
        port=8025,
        authenticator=handler.authenticator,
        auth_required=True,
        auth_require_tls=False,
        decode_data=False,
        enable_SMTPUTF8=True,
        data_size_limit=MAX_MESSAGE_BYTES,
    )
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    controller.start()
    try:
        stopped.wait()
    finally:
        controller.stop()


if __name__ == "__main__":
    try:
        serve()
    except Exception:
        raise SystemExit("Mail relay stopped: invalid configuration or startup failure.") from None
