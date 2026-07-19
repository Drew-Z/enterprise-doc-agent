from __future__ import annotations

import json
from pathlib import Path

from scripts.sanitize_deployment_evidence import sanitize_bytes, sanitize_directory


def test_sanitize_json_secret_and_sensitive_strings() -> None:
    payload = {
        "kind": "Secret",
        "metadata": {"name": "enterprise-doc-secrets"},
        "data": {"DATABASE__PASSWORD": "Y2FuYXJ5"},
        "note": "Authorization: Bearer canary-token",
        "url": "https://objects.example/upload?X-Amz-Signature=canary-signature",
    }
    result = sanitize_bytes(json.dumps(payload).encode(), suffix=".json").decode()
    assert "canary" not in result
    assert "enterprise-doc-secrets" in result
    assert "[REDACTED]" in result


def test_sanitize_yaml_secret_data_and_string_data() -> None:
    payload = b"""apiVersion: v1
kind: Secret
metadata:
  name: enterprise-doc-secrets
data:
  DATABASE__PASSWORD: Y2FuYXJ5LXBhc3N3b3Jk
stringData:
  MODEL__API_KEY: canary-api-key
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: runtime
data:
  endpoint: https://objects.example/upload?X-Amz-Signature=canary-signature
"""

    result = sanitize_bytes(payload, suffix=".yaml").decode()

    assert "canary" not in result
    assert "enterprise-doc-secrets" in result
    assert result.count("[REDACTED]") >= 3


def test_sanitize_directory_writes_hashed_inventory_without_source_values(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    destination = tmp_path / "sanitized"
    source.mkdir()
    (source / "events.txt").write_text(
        "Authorization: Bearer canary-token\npassword=canary-password\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"

    entries = sanitize_directory(source, destination, manifest)

    assert len(entries) == 1
    assert "canary" not in (destination / "events.txt").read_text(encoding="utf-8")
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["files"][0]["sha256"] == entries[0]["sha256"]
