"""Verify the public adapter and real SDK signatures with synthetic parent keys."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from scripts.server_backup import backup_daemon, host_environment, production_config

PARENT = {
    "endpoint": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
    "bucket": "synthetic-backup-test",
    "access": "b" * 32,
    "secret": "c" * 64,
    "region": "auto",
}


def test_adapter_emits_verifiable_scoped_session_without_parent_secret(monkeypatch):
    monkeypatch.setattr(host_environment, "protected_json", lambda _: dict(PARENT))
    result = host_environment.target_environment(production_config.ROOT + "/target.json")
    token = base64.b64decode(result["session_token"], validate=True).decode()
    assert token.startswith("jwt/")
    encoded = token[4:]
    claims = jwt.decode(
        encoded,
        PARENT["secret"],
        algorithms=["HS256"],
        audience=urlsplit(PARENT["endpoint"]).hostname,
        issuer=PARENT["access"],
    )
    assert claims["sub"] == "a" * 32
    assert claims["bucket"] == PARENT["bucket"]
    # Live R2 rejects simultaneous scope + actions with InvalidArgument even
    # though the vendor example combines them. Keep only the precise actions.
    assert "scope" not in claims
    assert set(claims["actions"]) == {"ListObjectsV2", "HeadObject", "GetObject", "PutObject"}
    assert claims["paths"] == {"prefixPaths": ["operations-recovery/v1/"], "objectPaths": []}
    assert claims["exp"] - claims["iat"] == 900
    assert result["expires_at"] == claims["exp"]
    assert result["secret"] == hashlib.sha256(encoded.encode()).hexdigest()
    assert result["access"] == PARENT["access"]
    assert PARENT["secret"] not in json.dumps(result)


def test_real_s3_presigning_carries_the_session_token():
    from scripts.server_backup.target_credentials import publication_credentials

    value = publication_credentials(PARENT)
    client = backup_daemon.storage(
        value["endpoint"], value["access"], value["secret"], session_token=value["session_token"]
    )
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": PARENT["bucket"],
                "Key": "operations-recovery/v1/snapshot-test.tar.age",
            },
            ExpiresIn=60,
        )
        query = parse_qs(urlsplit(url).query)
        assert query["X-Amz-Security-Token"] == [value["session_token"]]
        assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert PARENT["secret"] not in url
    finally:
        client.close()


@pytest.mark.parametrize("invalid", ["missing", "expired"])
def test_production_publisher_refuses_missing_or_expired_sessions_before_s3(monkeypatch, invalid):
    from scripts.server_backup.target_credentials import publication_credentials

    value = dict(PARENT) if invalid == "missing" else publication_credentials(PARENT, now=100)
    service = backup_daemon.BackupService.__new__(backup_daemon.BackupService)
    service.config = {
        "config_profile": "production",
        "target_environment_command": ["synthetic"],
        "target_endpoint": PARENT["endpoint"],
        "target_bucket": PARENT["bucket"],
    }
    monkeypatch.setattr(backup_daemon, "environment", lambda _: value)
    monkeypatch.setattr(
        backup_daemon, "storage", lambda *a, **k: pytest.fail("must not attempt S3")
    )
    with pytest.raises(production_config.ConfigurationError):
        service.publish()


def test_each_adapter_call_renews_without_network_or_parent_mutation(monkeypatch):
    from scripts.server_backup import target_credentials

    monkeypatch.setattr(host_environment, "protected_json", lambda _: PARENT)
    before = dict(PARENT)
    monkeypatch.setattr(target_credentials.time, "time", lambda: 1000)
    first = host_environment.target_environment(production_config.ROOT + "/target.json")
    monkeypatch.setattr(target_credentials.time, "time", lambda: 2000)
    renewed = host_environment.target_environment(production_config.ROOT + "/target.json")
    with pytest.raises(production_config.ConfigurationError):
        target_credentials.validate_session(first)
    assert target_credentials.validate_session(renewed) == renewed
    assert renewed["expires_at"] - first["expires_at"] == 1000
    assert renewed["session_token"] != first["session_token"]
    assert PARENT == before


@pytest.mark.parametrize(
    "change",
    [
        {"actions": ["GetObject", "PutObject", "DeleteObject"]},
        {"paths": {"prefixPaths": [""], "objectPaths": []}},
        {"bucket": "another-bucket"},
        {"aud": "another-authority"},
        {"scope": "admin-read-write"},
        {"iat": True},
    ],
)
def test_consumer_rejects_even_signed_overbroad_or_misbound_sessions(change):
    from scripts.server_backup.target_credentials import publication_credentials, validate_session

    value = publication_credentials(PARENT, now=1000)
    original = base64.b64decode(value["session_token"]).decode()[4:]
    claims = jwt.decode(original, options={"verify_signature": False})
    encoded = jwt.encode({**claims, **change}, PARENT["secret"], algorithm="HS256")
    value["secret"] = hashlib.sha256(encoded.encode()).hexdigest()
    value["session_token"] = base64.b64encode(("jwt/" + encoded).encode()).decode()
    with pytest.raises(production_config.ConfigurationError):
        validate_session(value, now=1000)


@pytest.mark.parametrize(
    "change",
    [
        {"access": "unknown"},
        {"secret": "short"},
        {"region": "other"},
        {"actions": ["DeleteObject"]},
        {"prefix": ""},
    ],
)
def test_parent_file_cannot_change_fixed_delegation_policy(change):
    from scripts.server_backup.target_credentials import publication_credentials

    with pytest.raises(production_config.ConfigurationError):
        publication_credentials({**PARENT, **change})


def test_valid_production_session_reaches_publisher_storage_boundary(monkeypatch):
    from scripts.server_backup import remote_retention
    from scripts.server_backup.target_credentials import publication_credentials
    from tests.deployment.test_server_backup_retention import Storage, kwargs

    value = publication_credentials(PARENT)
    service = backup_daemon.BackupService.__new__(backup_daemon.BackupService)
    service.config = {
        "config_profile": "production",
        "target_environment_command": ["synthetic"],
        "target_endpoint": PARENT["endpoint"],
        "target_bucket": PARENT["bucket"],
        "target_prefix": production_config.PREFIX,
        "remote_max_bytes": 1,
    }
    store = Storage()
    store.close = lambda: None
    observed = []

    def storage(*args, **options):
        observed.append((args, options))
        return store

    monkeypatch.setattr(backup_daemon, "environment", lambda _: value)
    monkeypatch.setattr(backup_daemon, "storage", storage)
    with pytest.raises(remote_retention.RemoteBudgetExceeded):
        service.publish(**kwargs())
    assert len(observed) == 1
    assert observed[0][1]["session_token"] == value["session_token"]
    assert observed[0][0][2] != PARENT["secret"]
    assert not store.puts
