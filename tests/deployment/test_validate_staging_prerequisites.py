from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate_staging_prerequisites.py"
SPEC = spec_from_file_location("validate_staging_prerequisites_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validate_staging_prerequisites = module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_staging_prerequisites
SPEC.loader.exec_module(validate_staging_prerequisites)


def _approval_annotations() -> dict[str, str]:
    return {
        "enterprise-doc-agent/deployment-profile": "tiny-single-node",
        "enterprise-doc-agent/prerequisites-sha256": "a" * 64,
        "enterprise-doc-agent/approved-staging-host": "staging.example.com",
        "enterprise-doc-agent/approved-tls-secret-name": "enterprise-doc-staging-tls",
        "enterprise-doc-agent/approved-database-egress-cidr": "8.8.8.8/32",
        "enterprise-doc-agent/approved-object-store-endpoint": (
            "https://objects.internal.example.com"
        ),
        "enterprise-doc-agent/approved-object-store-presign-endpoint": (
            "https://objects.example.com"
        ),
        "enterprise-doc-agent/approved-web-object-store-origins": ("https://objects.example.com"),
        "enterprise-doc-agent/approved-model-provider": "openai_compatible",
        "enterprise-doc-agent/approved-model-base-url": "https://model.example.com/v1",
        "enterprise-doc-agent/approved-model-name": "staging-model",
        "enterprise-doc-agent/approved-config-sha256": "b" * 64,
        "enterprise-doc-agent/approved-api-images": (
            "registry.example.com/enterprise-doc-api@sha256:" + "1" * 64
        ),
        "enterprise-doc-agent/approved-worker-images": (
            "registry.example.com/enterprise-doc-worker@sha256:" + "2" * 64
        ),
        "enterprise-doc-agent/approved-consumer-images": (
            "registry.example.com/enterprise-doc-consumer@sha256:" + "3" * 64
        ),
        "enterprise-doc-agent/approved-web-images": (
            "registry.example.com/enterprise-doc-web@sha256:" + "4" * 64
        ),
        "enterprise-doc-agent/approved-prometheus-images": (
            "quay.io/prometheus/prometheus@sha256:"
            "63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996"
        ),
    }


def _write_expected(path: Path) -> None:
    path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": "enterprise-doc-agent-staging",
                        "annotations": _approval_annotations(),
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "enterprise-doc-config",
                        "namespace": "enterprise-doc-agent-staging",
                    },
                    "data": {"MODEL__PROVIDER": "openai_compatible"},
                },
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": "enterprise-doc-prometheus-data",
                        "namespace": "enterprise-doc-agent-staging",
                    },
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": "local-path",
                        "volumeMode": "Filesystem",
                        "resources": {"requests": {"storage": "5Gi"}},
                    },
                },
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_live_manifest(path: Path, expected: Path, *, config_value: str | None = None) -> None:
    documents = [
        document
        for document in yaml.safe_load_all(expected.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]
    for index, document in enumerate(documents, start=1):
        metadata = document["metadata"]
        metadata["creationTimestamp"] = "2026-07-22T00:00:00Z"
        metadata["resourceVersion"] = str(index)
        metadata["uid"] = f"00000000-0000-0000-0000-{index:012d}"
        metadata.setdefault("annotations", {})[
            "kubectl.kubernetes.io/last-applied-configuration"
        ] = "redacted-runtime-value"
        if document["kind"] == "Namespace":
            metadata.setdefault("labels", {})["kubernetes.io/metadata.name"] = metadata["name"]
            document["spec"] = {"finalizers": ["kubernetes"]}
        if document["kind"] == "ConfigMap" and config_value is not None:
            document["data"]["MODEL__PROVIDER"] = config_value
        if document["kind"] == "PersistentVolumeClaim":
            metadata["annotations"].update(
                {
                    "pv.kubernetes.io/bind-completed": "yes",
                    "volume.kubernetes.io/storage-provisioner": "rancher.io/local-path",
                }
            )
            metadata["finalizers"] = ["kubernetes.io/pvc-protection"]
            document["spec"]["volumeName"] = "pvc-runtime-generated"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "List",
                "items": documents,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_live(path: Path, annotations: dict[str, str]) -> None:
    path.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "enterprise-doc-agent-staging",
                    "annotations": annotations,
                },
            }
        ),
        encoding="utf-8",
    )


def _evaluator_bundle() -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "enterprise-doc-rag-quality-v2-bundle-20260813-26",
            "namespace": "enterprise-doc-agent-staging",
        },
        "data": {
            name: "synthetic-test-data"
            for name in (
                "data-retention-standard.txt",
                "employee-handbook.txt",
                "evaluate_staging_rag_quality.py",
                "incident-response-runbook.txt",
                "incluster_rag_quality_driver.py",
                "issue_staging_smoke_token.py",
                "procurement-policy.txt",
                "rag_quality_v2.json",
                "security-policy.txt",
                "service-level-agreement.txt",
                "staging_smoke.py",
                "travel-policy.txt",
                "vendor-contract.txt",
            )
        },
    }


def test_validate_prerequisites_accepts_matching_admin_approval(tmp_path: Path) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)

    report = validate_staging_prerequisites.validate_prerequisites(
        expected,
        live,
        live_manifest,
    )

    assert report == {
        "status": "passed",
        "namespace": "enterprise-doc-agent-staging",
        "approval_keys_checked": 17,
        "live_objects_checked": 3,
        "prerequisites_sha256": "a" * 64,
    }


def test_validate_prerequisites_ignores_exact_evaluator_bundle_inventory(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)
    payload = yaml.safe_load(live_manifest.read_text(encoding="utf-8"))
    payload["items"].append(_evaluator_bundle())
    live_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_staging_prerequisites.validate_prerequisites(
        expected,
        live,
        live_manifest,
    )

    assert report["live_objects_checked"] == 3


@pytest.mark.parametrize("mutation", ["unexpected_key", "missing_key", "wrong_name"])
def test_validate_prerequisites_rejects_evaluator_bundle_lookalike(
    tmp_path: Path,
    mutation: str,
) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)
    bundle = _evaluator_bundle()
    data = bundle["data"]
    metadata = bundle["metadata"]
    assert isinstance(data, dict)
    assert isinstance(metadata, dict)
    if mutation == "unexpected_key":
        data["unexpected.py"] = "not-approved"
    elif mutation == "missing_key":
        data.pop("rag_quality_v2.json")
    else:
        metadata["name"] = "enterprise-doc-rag-quality-v2-bundle-current"
    payload = yaml.safe_load(live_manifest.read_text(encoding="utf-8"))
    payload["items"].append(bundle)
    live_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match="resource inventory",
    ):
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )


@pytest.mark.parametrize(
    "key",
    [
        "enterprise-doc-agent/prerequisites-sha256",
        "enterprise-doc-agent/approved-model-base-url",
        "enterprise-doc-agent/approved-api-images",
    ],
)
def test_validate_prerequisites_rejects_missing_or_drifted_approval(
    tmp_path: Path,
    key: str,
) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    annotations = _approval_annotations()
    annotations[key] = "drifted-private-value"
    _write_live(live, annotations)
    _write_live_manifest(live_manifest, expected)

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match=key,
    ) as raised:
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )

    assert "drifted-private-value" not in str(raised.value)


def test_validate_prerequisites_requires_complete_expected_contract(tmp_path: Path) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    annotations = _approval_annotations()
    annotations.pop("enterprise-doc-agent/approved-consumer-images")
    expected.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "enterprise-doc-agent-staging",
                    "annotations": annotations,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match="approved-consumer-images",
    ):
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )


def test_validate_prerequisites_rejects_live_object_spec_drift(tmp_path: Path) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected, config_value="drifted-private-value")

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match="ConfigMap/enterprise-doc-agent-staging/enterprise-doc-config",
    ) as raised:
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )

    assert "drifted-private-value" not in str(raised.value)


def test_validate_prerequisites_rejects_live_resource_inventory_drift(tmp_path: Path) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)
    payload = yaml.safe_load(live_manifest.read_text(encoding="utf-8"))
    payload["items"].pop()
    live_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match="resource inventory",
    ):
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )


def test_validate_prerequisites_rejects_unexpected_live_network_resource(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "expected.yaml"
    live = tmp_path / "live.json"
    live_manifest = tmp_path / "live.yaml"
    _write_expected(expected)
    _write_live(live, _approval_annotations())
    _write_live_manifest(live_manifest, expected)
    payload = yaml.safe_load(live_manifest.read_text(encoding="utf-8"))
    payload["items"].append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "unexpected-public-ingress",
                "namespace": "enterprise-doc-agent-staging",
            },
            "spec": {},
        }
    )
    live_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        validate_staging_prerequisites.PrerequisiteValidationError,
        match="resource inventory",
    ):
        validate_staging_prerequisites.validate_prerequisites(
            expected,
            live,
            live_manifest,
        )
