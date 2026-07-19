from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

ALLOWED_NAMESPACE = "enterprise-doc-agent-staging"
ALLOWED_DEPLOYMENTS = frozenset(
    {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
    }
)
_REVISION_PATTERN = re.compile(r"^[1-9][0-9]*$")


def validate_rollback_target(*, namespace: str, revisions: Mapping[str, int]) -> None:
    if namespace != ALLOWED_NAMESPACE:
        raise ValueError(f"rollback is restricted to {ALLOWED_NAMESPACE}")
    if not revisions:
        raise ValueError("at least one deployment revision is required")
    unknown = set(revisions) - ALLOWED_DEPLOYMENTS
    if unknown:
        raise ValueError(f"unsupported deployment target(s): {', '.join(sorted(unknown))}")
    for deployment, revision in revisions.items():
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError(f"revision for {deployment} must be an integer")
        if not _REVISION_PATTERN.fullmatch(str(revision)):
            raise ValueError(f"revision for {deployment} must be positive")


def rollback_commands(*, namespace: str, revisions: Mapping[str, int]) -> list[list[str]]:
    validate_rollback_target(namespace=namespace, revisions=revisions)
    commands: list[list[str]] = []
    for deployment, revision in revisions.items():
        commands.append(
            [
                "kubectl",
                "-n",
                namespace,
                "rollout",
                "undo",
                f"deployment/{deployment}",
                f"--to-revision={revision}",
            ]
        )
        commands.append(
            [
                "kubectl",
                "-n",
                namespace,
                "rollout",
                "status",
                f"deployment/{deployment}",
                "--timeout=300s",
            ]
        )
    return commands


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate or execute a Kubernetes rollout rollback"
    )
    parser.add_argument("--namespace", default=ALLOWED_NAMESPACE)
    parser.add_argument(
        "--revision",
        action="append",
        default=[],
        metavar="DEPLOYMENT=REVISION",
        help="explicit target revision; may be repeated",
    )
    parser.add_argument(
        "--revisions-json-env",
        default="ROLLBACK_REVISIONS_JSON",
        help="environment variable containing a deployment-to-revision JSON object",
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument("--migration-revision", default="unspecified")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--record-path", type=Path)
    args = parser.parse_args()
    if args.revision and os.environ.get(args.revisions_json_env):
        raise SystemExit("use --revision or the revisions JSON environment, not both")
    if args.revision:
        revisions: dict[str, int] = {}
        for item in args.revision:
            deployment, separator, raw_revision = item.partition("=")
            if not separator:
                raise SystemExit("--revision must use DEPLOYMENT=REVISION")
            try:
                revisions[deployment] = int(raw_revision)
            except ValueError as error:
                raise SystemExit("revision must be an integer") from error
    else:
        raw_json = os.environ.get(args.revisions_json_env)
        if not raw_json:
            raise SystemExit(f"set {args.revisions_json_env} or pass --revision")
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as error:
            raise SystemExit("rollback revisions JSON is invalid") from error
        if not isinstance(decoded, dict):
            raise SystemExit("rollback revisions JSON must be an object")
        revisions = decoded
    try:
        commands = rollback_commands(namespace=args.namespace, revisions=revisions)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if not args.confirm:
        print(json.dumps({"dry_run": True, "commands": commands}, indent=2))
        return
    if shutil.which("kubectl") is None:
        raise SystemExit("kubectl is required")
    started_at = datetime.now(UTC).isoformat()
    try:
        for command in commands:
            subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit("rollout rollback failed") from error
    record = {
        "schema_version": 1,
        "operation": "kubernetes-rollout-rollback",
        "status": "passed",
        "namespace": args.namespace,
        "revisions": revisions,
        "reason": args.reason,
        "migration_revision": args.migration_revision,
        "operator": os.environ.get("GITHUB_ACTOR", "unknown"),
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "A rollout undo does not reverse destructive database migrations; schema "
            "compatibility must be reviewed separately."
        ],
    }
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.record_path is not None:
        args.record_path.parent.mkdir(parents=True, exist_ok=True)
        args.record_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
