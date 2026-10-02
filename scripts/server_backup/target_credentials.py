"""Issue fixed R2 publication sessions locally; never expose the parent secret.

The protected signer is trusted and still holds the bucket-scoped parent key.
R2, not local JWT decoding, verifies the signature and enforces delegated scope.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit

from .production_config import PREFIX, ConfigurationError, validate_target

TTL_SECONDS = 900
ACTIONS = ("ListObjectsV2", "HeadObject", "GetObject", "PutObject")
PARENT_FIELDS = {"endpoint", "bucket", "access", "secret", "region"}


def _require(value):
    if not value:
        raise ConfigurationError("backup target credential contract differs")


def _clock(now):
    stamp = int(time.time()) if now is None else now
    _require(type(stamp) is int and 0 < stamp < 2**53 - TTL_SECONDS)
    return stamp


def _base(value):
    validate_target(value["endpoint"], value["bucket"])
    _require(value["region"] == "auto")
    _require(isinstance(value["access"], str) and re.fullmatch(r"[a-f0-9]{32}", value["access"]))
    _require(isinstance(value["secret"], str) and re.fullmatch(r"[a-f0-9]{64}", value["secret"]))


def _part(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def publication_credentials(parent, *, now=None):
    """Re-sign per publication, including same-ciphertext retries after restart."""
    _require(isinstance(parent, dict) and set(parent) == PARENT_FIELDS)
    _base(parent)
    stamp = _clock(now)
    authority = urlsplit(parent["endpoint"]).hostname
    claims = {
        "sub": authority.split(".")[0],
        "iss": parent["access"],
        "aud": authority,
        "bucket": parent["bucket"],
        "scope": "object-read-write",
        "actions": list(ACTIONS),
        "paths": {"prefixPaths": [PREFIX], "objectPaths": []},
        "iat": stamp,
        "exp": stamp + TTL_SECONDS,
    }
    unsigned = _part({"alg": "HS256", "typ": "JWT"}) + b"." + _part(claims)
    # R2 signs with the UTF-8 hex secret string, not bytes.fromhex(secret).
    signature = hmac.digest(parent["secret"].encode(), unsigned, "sha256")
    token = unsigned + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
    return {
        "endpoint": parent["endpoint"],
        "bucket": parent["bucket"],
        "region": "auto",
        "access": parent["access"],
        "secret": hashlib.sha256(token).hexdigest(),
        "session_token": base64.b64encode(b"jwt/" + token).decode(),
        "expires_at": claims["exp"],
    }


def validate_session(value, *, now=None):
    """Reject missing/expired/overbroad sessions before S3; R2 authenticates them."""
    _require(
        isinstance(value, dict) and set(value) == PARENT_FIELDS | {"session_token", "expires_at"}
    )
    _base(value)
    stamp = _clock(now)
    _require(
        type(value["expires_at"]) is int and stamp < value["expires_at"] <= stamp + TTL_SECONDS
    )
    session = value["session_token"]
    _require(isinstance(session, str) and 1 <= len(session) <= 8192)
    try:
        raw = base64.b64decode(session, validate=True)
        _require(raw.startswith(b"jwt/"))
        token = raw[4:]
        _require(hashlib.sha256(token).hexdigest() == value["secret"])
        parts = token.split(b".")
        _require(len(parts) == 3 and all(re.fullmatch(rb"[A-Za-z0-9_-]+", part) for part in parts))
        header, claims = [
            json.loads(base64.urlsafe_b64decode(part + b"=" * (-len(part) % 4)))
            for part in parts[:2]
        ]
        _require(header == {"alg": "HS256", "typ": "JWT"})
        _require(
            isinstance(claims, dict)
            and set(claims)
            == {"sub", "iss", "aud", "bucket", "scope", "actions", "paths", "iat", "exp"}
        )
        authority = urlsplit(value["endpoint"]).hostname
        _require(
            claims["sub"] == authority.split(".")[0]
            and claims["iss"] == value["access"]
            and claims["aud"] == authority
        )
        _require(claims["bucket"] == value["bucket"] and claims["scope"] == "object-read-write")
        _require(
            claims["actions"] == list(ACTIONS)
            and claims["paths"] == {"prefixPaths": [PREFIX], "objectPaths": []}
        )
        _require(type(claims["iat"]) is int and type(claims["exp"]) is int)
        _require(
            claims["exp"] == value["expires_at"]
            and claims["exp"] - claims["iat"] == TTL_SECONDS
            and claims["iat"] <= stamp
        )
        _require(len(base64.urlsafe_b64decode(parts[2] + b"=" * (-len(parts[2]) % 4))) == 32)
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ConfigurationError("backup session token is invalid or overbroad") from None
    return value
