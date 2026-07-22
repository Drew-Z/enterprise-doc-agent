from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

NAMESPACE = "enterprise-doc-agent-staging"
PREREQUISITE_HASH_ANNOTATION = "enterprise-doc-agent/prerequisites-sha256"
REQUIRED_APPROVAL_ANNOTATIONS = frozenset(
    {
        "enterprise-doc-agent/deployment-profile",
        PREREQUISITE_HASH_ANNOTATION,
        "enterprise-doc-agent/approved-staging-host",
        "enterprise-doc-agent/approved-tls-secret-name",
        "enterprise-doc-agent/approved-database-egress-cidr",
        "enterprise-doc-agent/approved-object-store-endpoint",
        "enterprise-doc-agent/approved-object-store-presign-endpoint",
        "enterprise-doc-agent/approved-web-object-store-origins",
        "enterprise-doc-agent/approved-model-provider",
        "enterprise-doc-agent/approved-model-base-url",
        "enterprise-doc-agent/approved-model-name",
        "enterprise-doc-agent/approved-config-sha256",
        "enterprise-doc-agent/approved-api-images",
        "enterprise-doc-agent/approved-worker-images",
        "enterprise-doc-agent/approved-consumer-images",
        "enterprise-doc-agent/approved-web-images",
    }
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "Job"})
SERVER_METADATA_FIELDS = frozenset(
    {"creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"}
)
LAST_APPLIED_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"
IGNORED_LIVE_IDENTITIES = frozenset(
    {
        ("ConfigMap", NAMESPACE, "kube-root-ca.crt"),
        ("ServiceAccount", NAMESPACE, "default"),
        ("ServiceAccount", NAMESPACE, "enterprise-doc-staging-deployer"),
    }
)


class PrerequisiteValidationError(ValueError):
    pass


def _mapping(value: Any, *, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PrerequisiteValidationError(f"{description} must be a mapping")
    return value


def _documents(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for payload in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "List":
            documents.append(payload)
            continue
        items = payload.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise PrerequisiteValidationError("live prerequisite List items must be objects")
        documents.extend(items)
    return documents


def _identity(document: dict[str, Any]) -> tuple[str, str, str]:
    metadata = _mapping(document.get("metadata"), description="resource metadata")
    kind = document.get("kind")
    name = metadata.get("name")
    namespace = metadata.get("namespace", "")
    if not isinstance(kind, str) or not isinstance(name, str) or not isinstance(namespace, str):
        raise PrerequisiteValidationError("each prerequisite must have kind and metadata.name")
    return kind, namespace, name


def _canonical(document: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(document)
    normalized.pop("status", None)
    metadata = _mapping(normalized.get("metadata"), description="resource metadata")
    for key in SERVER_METADATA_FIELDS:
        metadata.pop(key, None)
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        annotations.pop(LAST_APPLIED_ANNOTATION, None)
        if not annotations:
            metadata.pop("annotations", None)
    return normalized


def _without_server_defaults(
    live_document: dict[str, Any],
    expected_document: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    live = _canonical(live_document)
    expected = _canonical(expected_document)
    kind, _, name = _identity(expected)

    if kind == "Namespace":
        live_metadata = _mapping(live.get("metadata"), description="live Namespace metadata")
        expected_metadata = _mapping(
            expected.get("metadata"), description="expected Namespace metadata"
        )
        live_labels = live_metadata.get("labels")
        expected_labels = expected_metadata.get("labels")
        if isinstance(live_labels, dict) and (
            not isinstance(expected_labels, dict)
            or "kubernetes.io/metadata.name" not in expected_labels
        ):
            if live_labels.get("kubernetes.io/metadata.name") == name:
                live_labels.pop("kubernetes.io/metadata.name")
                if not live_labels:
                    live_metadata.pop("labels")
        if "spec" not in expected:
            live_spec = live.get("spec")
            if isinstance(live_spec, dict) and live_spec.get("finalizers") == ["kubernetes"]:
                live_spec.pop("finalizers")
                if not live_spec:
                    live.pop("spec")

    if kind == "Service":
        live_spec = _mapping(live.get("spec"), description="live Service spec")
        expected_spec = _mapping(expected.get("spec"), description="expected Service spec")
        allocated_fields = {"clusterIP", "clusterIPs", "ipFamilies"}
        for key in allocated_fields:
            if key not in expected_spec:
                live_spec.pop(key, None)
        defaults = {
            "internalTrafficPolicy": "Cluster",
            "ipFamilyPolicy": "SingleStack",
            "sessionAffinity": "None",
            "type": "ClusterIP",
        }
        for key, default in defaults.items():
            if key not in expected_spec and live_spec.get(key) == default:
                live_spec.pop(key)
        live_ports = live_spec.get("ports")
        expected_ports = expected_spec.get("ports")
        if isinstance(live_ports, list) and isinstance(expected_ports, list):
            expected_by_name = {
                port.get("name"): port
                for port in expected_ports
                if isinstance(port, dict) and isinstance(port.get("name"), str)
            }
            for live_port in live_ports:
                if not isinstance(live_port, dict):
                    continue
                expected_port = expected_by_name.get(live_port.get("name"))
                if not isinstance(expected_port, dict):
                    continue
                if "protocol" not in expected_port and live_port.get("protocol") == "TCP":
                    live_port.pop("protocol")
                if "targetPort" not in expected_port and live_port.get(
                    "targetPort"
                ) == live_port.get("port"):
                    live_port.pop("targetPort")

    if kind == "PodDisruptionBudget":
        live_spec = _mapping(live.get("spec"), description="live PDB spec")
        expected_spec = _mapping(expected.get("spec"), description="expected PDB spec")
        if (
            "unhealthyPodEvictionPolicy" not in expected_spec
            and live_spec.get("unhealthyPodEvictionPolicy") == "IfHealthyBudget"
        ):
            live_spec.pop("unhealthyPodEvictionPolicy")

    return live, expected


def _validate_live_objects(expected_manifest: Path, live_manifest: Path) -> int:
    expected_documents = _documents(expected_manifest)
    live_documents = [
        document
        for document in _documents(live_manifest)
        if _identity(document) not in IGNORED_LIVE_IDENTITIES
    ]
    if any(document.get("kind") in WORKLOAD_KINDS for document in expected_documents):
        raise PrerequisiteValidationError("expected prerequisite manifest contains a workload")
    expected_by_identity = {_identity(document): document for document in expected_documents}
    live_by_identity = {_identity(document): document for document in live_documents}
    if len(expected_by_identity) != len(expected_documents):
        raise PrerequisiteValidationError("expected prerequisite manifest contains duplicates")
    if len(live_by_identity) != len(live_documents):
        raise PrerequisiteValidationError("live prerequisite manifest contains duplicates")
    if expected_by_identity.keys() != live_by_identity.keys():
        raise PrerequisiteValidationError(
            "live prerequisite resource inventory does not match expected"
        )
    for identity, expected in expected_by_identity.items():
        live, normalized_expected = _without_server_defaults(
            live_by_identity[identity],
            expected,
        )
        if live != normalized_expected:
            kind, namespace, name = identity
            resource = f"{kind}/{namespace + '/' if namespace else ''}{name}"
            raise PrerequisiteValidationError(f"live prerequisite drift: {resource}")
    return len(expected_documents)


def _expected_namespace(path: Path) -> dict[str, Any]:
    documents = _documents(path)
    matches = [
        document
        for document in documents
        if document.get("kind") == "Namespace"
        and isinstance(document.get("metadata"), dict)
        and document["metadata"].get("name") == NAMESPACE
    ]
    if len(matches) != 1:
        raise PrerequisiteValidationError(
            f"expected manifest must contain exactly one Namespace/{NAMESPACE}"
        )
    return matches[0]


def validate_prerequisites(
    expected_manifest: Path,
    live_namespace_json: Path,
    live_manifest: Path,
) -> dict[str, object]:
    live_objects_checked = _validate_live_objects(expected_manifest, live_manifest)
    expected = _expected_namespace(expected_manifest)
    expected_metadata = _mapping(expected.get("metadata"), description="expected metadata")
    expected_annotations = _mapping(
        expected_metadata.get("annotations"),
        description="expected Namespace annotations",
    )
    missing = REQUIRED_APPROVAL_ANNOTATIONS - set(expected_annotations)
    if missing:
        raise PrerequisiteValidationError(
            f"expected Namespace is missing approval annotation: {sorted(missing)[0]}"
        )
    expected_digest = expected_annotations[PREREQUISITE_HASH_ANNOTATION]
    if not isinstance(expected_digest, str) or not SHA256.fullmatch(expected_digest):
        raise PrerequisiteValidationError("expected prerequisite fingerprint is not a SHA-256")

    live = json.loads(live_namespace_json.read_text(encoding="utf-8"))
    live_metadata = _mapping(live.get("metadata"), description="live metadata")
    if live.get("kind") != "Namespace" or live_metadata.get("name") != NAMESPACE:
        raise PrerequisiteValidationError(f"live object must be Namespace/{NAMESPACE}")
    live_annotations = _mapping(
        live_metadata.get("annotations"),
        description="live Namespace annotations",
    )
    for key in sorted(REQUIRED_APPROVAL_ANNOTATIONS):
        if live_annotations.get(key) != expected_annotations[key]:
            raise PrerequisiteValidationError(f"live approval does not match expected key: {key}")

    return {
        "status": "passed",
        "namespace": NAMESPACE,
        "approval_keys_checked": len(REQUIRED_APPROVAL_ANNOTATIONS),
        "live_objects_checked": live_objects_checked,
        "prerequisites_sha256": expected_digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the administrator-owned staging prerequisite approval"
    )
    parser.add_argument("--expected-manifest", type=Path, required=True)
    parser.add_argument("--live-manifest", type=Path, required=True)
    parser.add_argument("--live-namespace-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = validate_prerequisites(
            args.expected_manifest,
            args.live_namespace_json,
            args.live_manifest,
        )
    except (OSError, json.JSONDecodeError, yaml.YAMLError, PrerequisiteValidationError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
