from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_staging_admission.py"
SPEC = spec_from_file_location("verify_staging_admission_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verify_staging_admission = module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_staging_admission
SPEC.loader.exec_module(verify_staging_admission)


def test_isolated_guardrails_use_unique_policy_and_binding_names(tmp_path: Path) -> None:
    source = tmp_path / "guardrails.yaml"
    source.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "ValidatingAdmissionPolicy",
                    "metadata": {"name": "staging-policy"},
                    "spec": {"validations": [{"expression": "true"}]},
                },
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "ValidatingAdmissionPolicyBinding",
                    "metadata": {"name": "staging-policy"},
                    "spec": {
                        "policyName": "staging-policy",
                        "validationActions": ["Deny"],
                    },
                },
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    documents = verify_staging_admission._isolated_guardrails(source, suffix="verify-123")

    assert [item["metadata"]["name"] for item in documents] == [
        "staging-policy-verify-123",
        "staging-policy-verify-123",
    ]
    assert documents[1]["spec"]["policyName"] == "staging-policy-verify-123"


def test_namespace_approval_patch_uses_only_managed_non_secret_annotations() -> None:
    annotations = {
        "enterprise-doc-agent/deployment-profile": "tiny-single-node",
        "enterprise-doc-agent/prerequisites-sha256": "a" * 64,
        "enterprise-doc-agent/approved-api-images": "registry.example/api@sha256:" + "1" * 64,
        "unrelated.example/value": "keep-out",
    }
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": "enterprise-doc-agent-staging",
            "annotations": annotations,
        },
    }

    patch = verify_staging_admission._namespace_approval_patch(namespace)

    assert patch == {
        "metadata": {
            "annotations": {
                "enterprise-doc-agent/prerequisites-sha256": "a" * 64,
                "enterprise-doc-agent/approved-api-images": (
                    "registry.example/api@sha256:" + "1" * 64
                ),
            }
        }
    }
    assert "deployment-profile" not in str(patch)
    assert "unrelated.example/value" not in str(patch)


def test_namespace_approval_patch_requires_fingerprint() -> None:
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "enterprise-doc-agent-staging", "annotations": {}},
    }

    with pytest.raises(ValueError, match="prerequisites-sha256"):
        verify_staging_admission._namespace_approval_patch(namespace)
