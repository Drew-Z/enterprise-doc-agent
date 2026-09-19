from __future__ import annotations

import json
from dataclasses import replace
from email.message import EmailMessage

import httpx
import pytest
from infra.identity.mail_relay import MAX_MESSAGE_BYTES, MailRelay, RelayConfig


def configuration() -> RelayConfig:
    return RelayConfig(
        worker_origin="https://inbox.ciallobill.ccwu.cc",
        sender="noreply@notify.ciallobill.ccwu.cc",
        recipients=("owner01@mailtest.ciallobill.ccwu.cc",),
        site_password="site-password-fixture-value-00000000",
        address_token="address-jwt-fixture-value-0000000000",
        smtp_password="smtp-password-fixture-value-00000000",
    )


def verification_mail() -> bytes:
    message = EmailMessage()
    message["From"] = "DocAgent <noreply@notify.ciallobill.ccwu.cc>"
    message["To"] = "owner01@mailtest.ciallobill.ccwu.cc"
    message["Subject"] = "验证邮箱"
    message.set_content("请验证邮箱")
    message.add_alternative(
        '<a href="https://auth.example/verify?key=private-link">验证</a>', subtype="html"
    )
    return message.as_bytes()


async def test_private_mail_uses_site_password_and_address_token() -> None:
    observed: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"status": "ok"})

    config = configuration()
    relay = MailRelay(config, transport=httpx.MockTransport(receive))
    result = await relay.submit(verification_mail(), config.sender, list(config.recipients))

    assert result == "250 Message accepted"
    assert len(observed) == 1
    assert str(observed[0].url) == config.worker_origin + "/external/api/send_mail"
    assert observed[0].headers["x-custom-auth"] == config.site_password
    payload = json.loads(observed[0].content)
    assert payload["token"] == config.address_token
    assert payload["subject"] == "验证邮箱"
    assert payload["is_html"] is True
    assert "private-link" in payload["content"]
    assert payload["to_mail"] == config.recipients[0]
    assert config.smtp_password not in observed[0].content.decode()


@pytest.mark.parametrize(
    ("sender", "recipients"),
    [
        ("attacker@example.com", ["owner01@mailtest.ciallobill.ccwu.cc"]),
        ("noreply@notify.ciallobill.ccwu.cc", ["unapproved@example.com"]),
        ("noreply@notify.ciallobill.ccwu.cc", []),
        (
            "noreply@notify.ciallobill.ccwu.cc",
            ["owner01@mailtest.ciallobill.ccwu.cc", "unapproved@example.com"],
        ),
    ],
)
async def test_unapproved_envelope_never_reaches_mail_service(
    sender: str, recipients: list[str]
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Unapproved envelope reached the mail provider")

    result = await MailRelay(configuration(), transport=httpx.MockTransport(unexpected)).submit(
        verification_mail(), sender, recipients
    )
    assert result == "550 Envelope not allowed"


@pytest.mark.parametrize(
    "raw",
    [
        b"x" * (MAX_MESSAGE_BYTES + 1),
        b"From: someone@example.com\r\nSubject: reset\r\n\r\nsecret",
        b"From: noreply@notify.ciallobill.ccwu.cc\r\n\r\nmissing subject",
        (
            b"From: noreply@notify.ciallobill.ccwu.cc\r\nSubject: a\r\nSubject: b\r\n"
            b"Content-Type: text/plain; charset=unknown-charset\r\n\r\nx"
        ),
    ],
    ids=["oversized", "wrong-from", "missing-subject", "duplicate-headers"],
)
async def test_invalid_or_oversized_email_never_reaches_provider(raw: bytes) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid message reached the mail provider")

    config = configuration()
    result = await MailRelay(config, transport=httpx.MockTransport(unexpected)).submit(
        raw, config.sender, list(config.recipients)
    )
    assert result.startswith(("552 ", "554 "))


@pytest.mark.parametrize(
    "changes",
    [
        {"worker_origin": "http://public.example"},
        {"worker_origin": "https://name:password@public.example"},
        {"worker_origin": "https://@public.example"},
        {"worker_origin": "https://:@public.example"},
        {"worker_origin": "https://public.example/path?secret=yes"},
        {"worker_origin": "http://public.example", "test_capture": True},
        {"recipients": ()},
        {"site_password": ""},
        {"sender": "Display Name <noreply@notify.ciallobill.ccwu.cc>"},
    ],
)
def test_unsafe_configuration_is_rejected_without_secret_echo(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Invalid mail relay configuration"):
        replace(configuration(), **changes)


@pytest.mark.parametrize("failure", ["401", "redirect", "invalid_json", "large", "timeout"])
async def test_provider_failures_are_bounded_and_do_not_leak_secrets(
    failure: str, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0
    secret = "PRIVATE-RESET-LINK-AND-TOKEN"

    def receive(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout(secret)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example/" + secret})
        if failure == "401":
            return httpx.Response(401, text=secret)
        if failure == "large":
            return httpx.Response(200, content=b" " * 4097)
        return httpx.Response(200, text=secret)

    config = configuration()
    result = await MailRelay(config, transport=httpx.MockTransport(receive)).submit(
        verification_mail(), config.sender, list(config.recipients)
    )
    assert result == "451 Mail service unavailable"
    assert calls == 1
    captured = capsys.readouterr()
    assert not captured.out and not captured.err
    assert config.site_password not in repr(config)
    assert config.address_token not in repr(config)
