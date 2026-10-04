"""Strict production configuration and protected local credential-file reading."""

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = "/var/lib/enterprise-doc-backup"
NAMESPACE = "enterprise-doc-agent-staging"
PREFIX = "operations-recovery/v1/"
MAX_BYTES = 3 * 1024**3
SHA = re.compile(r"[a-f0-9]{64}")


class ConfigurationError(ValueError):
    """Redacted invalid configuration; do not include credential values."""


def validate_target(endpoint, bucket):
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or re.fullmatch(r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", parsed.hostname or "")
            is None
            or not isinstance(bucket, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ConfigurationError("invalid production backup target") from None


def validate_config(config, *, expected_root=ROOT, expected_namespace=NAMESPACE):
    required = {
        "schema_version",
        "config_profile",
        "installation_root",
        "package_id",
        "state_directory",
        "age_path",
        "age_sha256",
        "public_recipient",
        "source_namespace",
        "source_environment_command",
        "target_environment_command",
        "target_endpoint",
        "target_bucket",
        "target_prefix",
        "remote_max_bytes",
        "interval_seconds",
        "release",
        "extra_artifact_ids",
    }
    optional = {"publication_multipart_enabled"}
    if not isinstance(config, dict) or not required <= set(config) <= required | optional:
        raise ConfigurationError("production configuration fields differ")
    multipart = config.get("publication_multipart_enabled", False)
    if type(multipart) is not bool:
        raise ConfigurationError("invalid publication transport option")
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["config_profile"] != "production"
    ):
        raise ConfigurationError("production configuration profile is required")
    root = PurePosixPath(expected_root)
    if (
        not root.is_absolute()
        or ".." in root.parts
        or config["installation_root"] != str(root)
        or config["source_namespace"] != expected_namespace
        or not isinstance(config["package_id"], str)
        or SHA.fullmatch(config["package_id"]) is None
        or not isinstance(config["age_sha256"], str)
        or SHA.fullmatch(config["age_sha256"]) is None
        or config["state_directory"] != str(root / "state")
        or config["age_path"] != str(root / "releases" / config["package_id"] / "age")
    ):
        raise ConfigurationError("production path or source binding differs")
    command = ["/usr/bin/python3", "-B", "-m", "server_backup.host_environment"]
    if config["source_environment_command"] != [
        *command,
        "source",
        "--namespace",
        expected_namespace,
    ] or config["target_environment_command"] != [
        *command,
        "target",
        "--file",
        str(root / "target.json"),
        *(["--multipart"] if multipart else []),
    ]:
        raise ConfigurationError("production credential command differs")
    if (
        type(config["interval_seconds"]) is not int
        or not 60 <= config["interval_seconds"] <= 300
        or type(config["remote_max_bytes"]) is not int
        or not 1 <= config["remote_max_bytes"] <= MAX_BYTES
        or config["target_prefix"] != PREFIX
        or not isinstance(config["public_recipient"], str)
        or re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", config["public_recipient"])
        is None
    ):
        raise ConfigurationError("production capture bounds differ")
    validate_target(config["target_endpoint"], config["target_bucket"])
    release = config["release"]
    if (
        not isinstance(release, dict)
        or set(release) != {"release", "source", "revision"}
        or not isinstance(release["release"], str)
        or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", release["release"]) is None
        or not isinstance(release["source"], str)
        or re.fullmatch(r"[a-f0-9]{40}", release["source"]) is None
        or release["revision"] != "20260924_0031"
    ):
        raise ConfigurationError("production release binding differs")
    extra = config["extra_artifact_ids"]
    if (
        not isinstance(extra, list)
        or len(extra) > 20
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", value)
            is None
            for value in extra
        )
    ):
        raise ConfigurationError("invalid explicit historical artifact references")
    return config


def protected_json(path):
    """Read a regular root-owned 0600 file without following any symlink."""
    if os.name != "posix":
        raise ConfigurationError("production credentials require a native Linux host")
    path = Path(path)
    if not path.is_absolute() or any(parent.is_symlink() for parent in [path, *path.parents]):
        raise ConfigurationError("protected file path is invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            meta = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(meta.st_mode)
                or meta.st_uid != 0
                or meta.st_mode & 0o077
                or not 1 <= meta.st_size <= 65536
            ):
                raise ConfigurationError("protected file ownership or size differs")
            raw = source.read(65537)
            if len(raw) != meta.st_size:
                raise ConfigurationError("protected file changed during read")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ConfigurationError("protected file must contain an object")
        return value
    except (OSError, ValueError):
        raise ConfigurationError("protected configuration could not be read") from None
