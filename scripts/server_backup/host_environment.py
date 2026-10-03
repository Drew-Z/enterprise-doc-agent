"""Narrow Linux credential adapter; stdout is consumed in memory by the daemon."""

import argparse
import json
import os

from .production_config import NAMESPACE, ROOT, ConfigurationError, protected_json, validate_target
from .server_capture import limited_output
from .target_credentials import publication_credentials

SOURCE_FIELDS = (
    "DATABASE__URL",
    "OBJECT_STORE__ENDPOINT",
    "OBJECT_STORE__ACCESS_KEY",
    "OBJECT_STORE__SECRET_KEY",
    "OBJECT_STORE__DOCUMENTS_BUCKET",
    "OBJECT_STORE__ARTIFACTS_BUCKET",
)


def source_environment(namespace):
    if namespace != NAMESPACE:
        raise ConfigurationError("unapproved production source namespace")
    query = (
        "import json,os;print(json.dumps({k:os.environ[k] for k in " + repr(SOURCE_FIELDS) + "}))"
    )
    raw = limited_output(
        [
            "/usr/local/bin/k3s",
            "kubectl",
            "-n",
            namespace,
            "exec",
            "deployment/enterprise-doc-api",
            "--",
            "python",
            "-c",
            query,
        ],
        env=os.environ.copy(),
        timeout=20,
        max_bytes=65536,
    )
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != set(SOURCE_FIELDS):
        raise ConfigurationError("source credential contract differs")
    if any(not isinstance(item, str) or not 1 <= len(item) <= 8192 for item in value.values()):
        raise ConfigurationError("source credential bounds differ")
    return {
        "database_url": value["DATABASE__URL"],
        "object_endpoint": value["OBJECT_STORE__ENDPOINT"],
        "object_access": value["OBJECT_STORE__ACCESS_KEY"],
        "object_secret": value["OBJECT_STORE__SECRET_KEY"],
        "documents_bucket": value["OBJECT_STORE__DOCUMENTS_BUCKET"],
        "artifacts_bucket": value["OBJECT_STORE__ARTIFACTS_BUCKET"],
        "object_region": "auto",
    }


def target_environment(path):
    if str(path) != ROOT + "/target.json":
        raise ConfigurationError("unapproved target credential file")
    value = protected_json(path)
    if set(value) != {"endpoint", "bucket", "access", "secret", "region"}:
        raise ConfigurationError("target credential fields differ")
    validate_target(value["endpoint"], value["bucket"])
    if value["region"] != "auto" or any(
        not isinstance(value[key], str) or not 1 <= len(value[key]) <= 8192
        for key in ("access", "secret")
    ):
        raise ConfigurationError("target credential bounds differ")
    return publication_credentials(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("source", "target"))
    parser.add_argument("--namespace")
    parser.add_argument("--file")
    args = parser.parse_args()
    try:
        if args.kind == "source" and args.namespace and not args.file:
            value = source_environment(args.namespace)
        elif args.kind == "target" and args.file and not args.namespace:
            value = target_environment(args.file)
        else:
            raise ConfigurationError("invalid credential command")
    except Exception:
        raise SystemExit("backup credential acquisition failed") from None
    print(json.dumps(value))


if __name__ == "__main__":
    main()
