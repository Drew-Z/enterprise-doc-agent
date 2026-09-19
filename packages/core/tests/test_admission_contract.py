from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    credential_digest,
    prepare_admission_credential,
    require_operator,
)
from enterprise_doc_core.admission.errors import AdmissionDenied, AdmissionForbidden


def request_values() -> dict[str, object]:
    return {
        "recipient_email": " Owner@Example.TEST ",
        "issuer": "https://identity.example.test",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "quota_bytes": 1024,
        "seat_limit": 1,
    }


def test_prepared_credentials_are_independent_and_redacted() -> None:
    first, second = prepare_admission_credential(), prepare_admission_credential()
    plaintext = first.token.get_secret_value()
    assert first.grant_id != second.grant_id
    assert plaintext != second.token.get_secret_value()
    assert plaintext.startswith("adm1_") and len(plaintext) == 48
    assert plaintext not in repr(first)
    assert plaintext not in str(asdict(first))
    assert len(credential_digest(first.token)) == 64
    assert credential_digest(first.token) != credential_digest(second.token)


@pytest.mark.parametrize("token", ["", "adm1_short", "adm1_" + "a" * 42, "adm1_" + "!" * 43])
def test_malformed_secret_errors_never_echo_input(token: str) -> None:
    from pydantic import SecretStr

    with pytest.raises(AdmissionDenied) as error:
        credential_digest(SecretStr(token))
    assert str(error.value) == "admission_denied"


def test_grant_normalizes_email_without_normalizing_issuer() -> None:
    grant = AdmissionGrantRequest.model_validate(request_values())
    assert grant.recipient_email == "owner@example.test"
    assert grant.quota_bytes == 1024
    assert grant.seat_limit == 1
    with pytest.raises(ValidationError):
        AdmissionGrantRequest.model_validate(
            {**request_values(), "issuer": " https://identity.example.test "}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient_email", "missing-at.test"),
        ("recipient_email", "owner@example.test\nother@example.test"),
        ("issuer", ""),
        ("issuer", "https://identity.example.test\x00"),
        ("quota_bytes", 0),
        ("quota_bytes", 2**63),
        ("quota_bytes", True),
        ("seat_limit", 0),
        ("seat_limit", 2**31),
        ("seat_limit", True),
        ("expires_at", datetime(2026, 9, 13)),
    ],
)
def test_invalid_grant_cannot_reach_persistence(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AdmissionGrantRequest.model_validate({**request_values(), field: value})


def test_tenant_owner_input_is_not_a_platform_operator() -> None:
    for candidate in ("owner", {"role": "owner"}, None):
        with pytest.raises(AdmissionForbidden):
            require_operator(candidate)
    operator = PlatformAdmissionOperator(operator_id="local-operator", reason="Test admission")
    assert require_operator(operator) is operator
    with pytest.raises(AdmissionForbidden):
        require_operator(PlatformAdmissionOperator(operator_id="", reason=""))
