from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|authorization|cookie|credential)",
    re.IGNORECASE,
)
TEXT_SECRET = re.compile(
    r"(?i)(\b(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|authorization|cookie|credential)\b\s*[:=]\s*)([^\s,;]+)"
)
BEARER = re.compile(r"(?i)(\bBearer\s+)([^\s,;]+)")
SIGNED_QUERY = re.compile(
    r"(?i)([?&](?:x-amz-[^=&]+|signature|sig|token|credential|expires|x-goog-[^=&]+)=)([^&\s]+)"
)
DSN_PASSWORD = re.compile(r"(://[^/:\s]+:)([^@/\s]+)(@)")


def _redact_text(value: str) -> str:
    value = BEARER.sub(r"\1[REDACTED]", value)
    value = TEXT_SECRET.sub(r"\1[REDACTED]", value)
    value = SIGNED_QUERY.sub(r"\1[REDACTED]", value)
    return DSN_PASSWORD.sub(r"\1[REDACTED]\3", value)


def _redact_structured(value: Any, *, secret_context: bool = False) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        kind = str(value.get("kind", ""))
        is_secret = secret_context or kind.lower() == "secret"
        for key, item in value.items():
            key_text = str(key)
            if is_secret and key_text in {"data", "stringData"}:
                if isinstance(item, dict):
                    result[key_text] = {str(child): "[REDACTED]" for child in item}
                else:
                    result[key_text] = "[REDACTED]"
            elif SENSITIVE_KEY.search(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact_structured(item, secret_context=is_secret)
        return result
    if isinstance(value, list):
        return [_redact_structured(item, secret_context=secret_context) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def sanitize_bytes(data: bytes, *, suffix: str) -> bytes:
    text = data.decode("utf-8", errors="replace")
    normalized_suffix = suffix.lower()
    if normalized_suffix in {".json", ".yaml", ".yml"}:
        try:
            if normalized_suffix == ".json":
                parsed = json.loads(text)
                return (
                    json.dumps(_redact_structured(parsed), indent=2, sort_keys=True) + "\n"
                ).encode()
            documents = list(yaml.safe_load_all(text))
        except (json.JSONDecodeError, yaml.YAMLError):
            pass
        else:
            return (
                yaml.safe_dump_all(
                    [_redact_structured(document) for document in documents],
                    explicit_start=len(documents) > 1,
                    sort_keys=True,
                )
            ).encode("utf-8")
    return (_redact_text(text).rstrip("\n") + "\n").encode()


def sanitize_file(source: Path, destination: Path) -> dict[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sanitized = sanitize_bytes(source.read_bytes(), suffix=source.suffix)
    destination.write_bytes(sanitized)
    return {
        "source": source.as_posix(),
        "path": destination.as_posix(),
        "sha256": hashlib.sha256(sanitized).hexdigest(),
    }


def sanitize_directory(source: Path, destination: Path, manifest: Path) -> list[dict[str, str]]:
    if not source.is_dir():
        raise ValueError(f"input directory does not exist: {source}")
    entries: list[dict[str, str]] = []
    for file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = file.relative_to(source)
        entries.append(sanitize_file(file, destination / relative))
    if not entries:
        raise ValueError("input directory contains no evidence files")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema_version": 1, "files": entries}, indent=2) + "\n")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Redact deployment evidence without storing secret values"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.input_dir is not None:
        if args.output_dir is None or args.manifest is None:
            parser.error("--input-dir requires --output-dir and --manifest")
        sanitize_directory(args.input_dir, args.output_dir, args.manifest)
    elif args.input is not None and args.output is not None:
        sanitize_file(args.input, args.output)
    else:
        parser.error("provide either --input/--output or --input-dir/--output-dir/--manifest")


if __name__ == "__main__":
    main()
