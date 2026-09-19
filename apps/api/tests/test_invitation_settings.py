import pytest
from pydantic import ValidationError

from enterprise_doc_api.config import ApiSettings


def test_invitations_are_disabled_by_default() -> None:
    assert ApiSettings(_env_file=None).invitations.enabled is False


def test_invitations_require_browser_identity_configuration() -> None:
    with pytest.raises(ValidationError, match="invitations require browser authentication"):
        ApiSettings(_env_file=None, invitations={"enabled": True})


def test_invitation_lifetime_can_be_set_through_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVITATIONS__TTL_SECONDS", "3600")
    assert ApiSettings(_env_file=None).invitations.ttl_seconds == 3600


@pytest.mark.parametrize("value", ["59", "604801", "true", "1.5", "3600.0", True, 3600.5, 0])
def test_invitation_lifetime_rejects_out_of_range_and_non_integer_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(_env_file=None, invitations={"ttl_seconds": value})
