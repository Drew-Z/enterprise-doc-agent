from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEPLOYER = "system:serviceaccount:enterprise-doc-agent-staging:enterprise-doc-staging-deployer"
NAMESPACE = "enterprise-doc-agent-staging"
POLICY_CONFIG = "enterprise-doc-staging-policy"
APPROVAL_PREFIX = "enterprise-doc-agent/approved-"
PREREQUISITE_HASH_ANNOTATION = "enterprise-doc-agent/prerequisites-sha256"
SUFFIX_PATTERN = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


def _documents(path: Path) -> list[dict[str, Any]]:
    return [
        document
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]


def _resource(
    documents: list[dict[str, Any]],
    *,
    kind: str,
    name: str,
) -> dict[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and isinstance(document.get("metadata"), dict)
        and document["metadata"].get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {kind}/{name}")
    return matches[0]


def _run_kubectl(
    args: list[str],
    *,
    payload: str | None = None,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["kubectl", *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    if (completed.returncode == 0) != expect_success:
        raise RuntimeError(
            json.dumps(
                {
                    "args": args,
                    "expected_success": expect_success,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                indent=2,
            )
        )
    return completed


def _get_optional(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _dry_run(document: dict[str, Any], *, expect_success: bool) -> None:
    payload = yaml.safe_dump(document, sort_keys=False)
    completed = _run_kubectl(
        [
            "apply",
            "--dry-run=server",
            "--as",
            DEPLOYER,
            "-f",
            "-",
        ],
        payload=payload,
        expect_success=expect_success,
    )
    if not expect_success and "staging" not in completed.stderr.lower():
        raise RuntimeError(
            f"admission rejection did not identify staging policy: {completed.stderr}"
        )


def _isolated_guardrails(path: Path, *, suffix: str | None = None) -> list[dict[str, Any]]:
    suffix = suffix or f"verify-{uuid.uuid4().hex[:12]}"
    if len(suffix) > 40 or not SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError("guardrail verification suffix must be a DNS label fragment")
    documents = copy.deepcopy(_documents(path))
    policies = {
        document["metadata"]["name"]: f"{document['metadata']['name']}-{suffix}"
        for document in documents
        if document.get("kind") == "ValidatingAdmissionPolicy"
    }
    if not policies:
        raise ValueError("guardrail bundle contains no ValidatingAdmissionPolicy")
    for document in documents:
        kind = document.get("kind")
        metadata = document.get("metadata")
        if kind not in {"ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"}:
            raise ValueError(f"unexpected guardrail kind: {kind}")
        if not isinstance(metadata, dict) or metadata.get("name") not in policies:
            raise ValueError("guardrail policy and binding names must match")
        original_name = metadata["name"]
        metadata["name"] = policies[original_name]
        if kind == "ValidatingAdmissionPolicyBinding":
            spec = document.get("spec")
            if not isinstance(spec, dict) or spec.get("policyName") != original_name:
                raise ValueError("guardrail binding must reference its matching policy")
            spec["policyName"] = policies[original_name]
    return documents


def _namespace_approval_patch(namespace: dict[str, Any]) -> dict[str, Any]:
    metadata = namespace.get("metadata")
    if (
        namespace.get("kind") != "Namespace"
        or not isinstance(metadata, dict)
        or metadata.get("name") != NAMESPACE
    ):
        raise ValueError(f"expected Namespace/{NAMESPACE}")
    annotations = metadata.get("annotations")
    if not isinstance(annotations, dict):
        raise ValueError("rendered staging Namespace must contain approval annotations")
    managed = {
        key: value
        for key, value in annotations.items()
        if key == PREREQUISITE_HASH_ANNOTATION or key.startswith(APPROVAL_PREFIX)
    }
    if PREREQUISITE_HASH_ANNOTATION not in managed:
        raise ValueError(f"rendered staging Namespace is missing {PREREQUISITE_HASH_ANNOTATION}")
    return {"metadata": {"annotations": managed}}


def _assert_auth(*, verb: str, resource: str, expected: str) -> None:
    completed = subprocess.run(
        [
            "kubectl",
            "auth",
            "can-i",
            verb,
            resource,
            "--namespace",
            NAMESPACE,
            "--as",
            DEPLOYER,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = completed.stdout.strip()
    if actual != expected:
        raise RuntimeError(
            f"unexpected deployer authorization for {verb} {resource}: {actual or 'error'}"
        )


def verify(*, bootstrap_dir: Path, rendered_manifest: Path, smoke_job: Path) -> None:
    rendered = _documents(rendered_manifest)
    smoke = _documents(smoke_job)
    namespace = _resource(rendered, kind="Namespace", name=NAMESPACE)
    runtime_config = _resource(rendered, kind="ConfigMap", name="enterprise-doc-config")
    api = _resource(rendered, kind="Deployment", name="enterprise-doc-api")
    migration = _resource(rendered, kind="Job", name="enterprise-doc-migrate")
    readiness = _resource(smoke, kind="Job", name="m5-staging-smoke")

    namespace_before = _get_optional(["get", "namespace", NAMESPACE, "-o", "yaml"])
    if namespace_before.returncode != 0:
        raise RuntimeError(f"staging namespace does not exist: {namespace_before.stderr}")
    existing_namespace = yaml.safe_load(namespace_before.stdout)
    existing_metadata = existing_namespace.get("metadata")
    if not isinstance(existing_metadata, dict):
        raise RuntimeError("live staging Namespace metadata is invalid")
    existing_annotations = existing_metadata.get("annotations")
    if not isinstance(existing_annotations, dict):
        existing_annotations = {}
    approval_patch = _namespace_approval_patch(namespace)
    managed_approvals = approval_patch["metadata"]["annotations"]
    restore_patch = {
        "metadata": {
            "annotations": {
                key: existing_annotations.get(key) if key in existing_annotations else None
                for key in managed_approvals
            }
        }
    }
    guardrails = bootstrap_dir / "staging-deployer-guardrails.yaml"
    isolated_guardrails = _isolated_guardrails(guardrails)
    guardrail_payload = yaml.safe_dump_all(
        isolated_guardrails,
        sort_keys=False,
        explicit_start=True,
    )
    approvals_applied = False
    guardrails_applied = False
    try:
        _run_kubectl(
            [
                "patch",
                "namespace",
                NAMESPACE,
                "--type=merge",
                "-p",
                json.dumps(approval_patch, separators=(",", ":")),
            ]
        )
        approvals_applied = True
        _run_kubectl(["apply", "-f", "-"], payload=guardrail_payload)
        guardrails_applied = True

        for verb, resource, expected in (
            ("get", "secrets", "no"),
            ("create", "pods", "no"),
            ("update", "configmaps", "no"),
            ("patch", "networkpolicies.networking.k8s.io", "no"),
            ("create", "jobs.batch", "yes"),
            ("patch", "deployments.apps", "yes"),
            ("delete", "job/enterprise-doc-migrate", "yes"),
            ("delete", "job/m5-staging-smoke", "yes"),
            ("delete", "job/unreviewed-maintenance", "no"),
        ):
            _assert_auth(verb=verb, resource=resource, expected=expected)

        for document in (api, migration, readiness):
            _dry_run(document, expect_success=True)

        _dry_run(runtime_config, expect_success=False)

        unreviewed_secret = copy.deepcopy(api)
        unreviewed_secret["spec"]["template"]["spec"]["containers"][0]["envFrom"][1]["secretRef"][
            "name"
        ] = "enterprise-doc-staging-deployer-token"
        _dry_run(unreviewed_secret, expect_success=False)

        unreviewed_image = copy.deepcopy(api)
        unreviewed_image["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "ghcr.io/attacker/exfil@sha256:" + "0" * 64
        )
        _dry_run(unreviewed_image, expect_success=False)

    finally:
        if guardrails_applied:
            _run_kubectl(
                ["delete", "-f", "-", "--ignore-not-found"],
                payload=guardrail_payload,
            )
        if approvals_applied:
            _run_kubectl(
                [
                    "patch",
                    "namespace",
                    NAMESPACE,
                    "--type=merge",
                    "-p",
                    json.dumps(restore_patch, separators=(",", ":")),
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify staging admission allow/deny behavior against a real API server"
    )
    parser.add_argument("--bootstrap-dir", type=Path, required=True)
    parser.add_argument("--rendered-manifest", type=Path, required=True)
    parser.add_argument("--smoke-job", type=Path, required=True)
    args = parser.parse_args()
    verify(
        bootstrap_dir=args.bootstrap_dir,
        rendered_manifest=args.rendered_manifest,
        smoke_job=args.smoke_job,
    )
    print(json.dumps({"status": "passed"}))


if __name__ == "__main__":
    main()
