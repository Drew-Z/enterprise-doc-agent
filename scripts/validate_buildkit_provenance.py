from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class BuildKitProvenanceError(ValueError):
    """Raised when BuildKit provenance cannot be bound to a known SLSA schema."""


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_buildkit_provenance(path: Path) -> str:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildKitProvenanceError(f"invalid BuildKit provenance JSON: {path}") from error

    if not isinstance(payload, dict):
        raise BuildKitProvenanceError("BuildKit provenance must be a JSON object")

    has_v02_shape = "buildType" in payload
    has_v1_shape = "buildDefinition" in payload or "runDetails" in payload
    if has_v02_shape == has_v1_shape:
        raise BuildKitProvenanceError(
            "BuildKit provenance must match exactly one supported SLSA predicate schema"
        )

    is_v02 = has_v02_shape and _non_empty_string(payload.get("buildType"))
    build_definition = payload.get("buildDefinition")
    run_details = payload.get("runDetails")
    is_v1 = (
        has_v1_shape
        and isinstance(build_definition, dict)
        and _non_empty_string(build_definition.get("buildType"))
        and isinstance(run_details, dict)
        and isinstance(run_details.get("builder"), dict)
        and _non_empty_string(run_details["builder"].get("id"))
    )

    if not (is_v02 or is_v1):
        raise BuildKitProvenanceError(
            "BuildKit provenance is incomplete for its declared SLSA predicate schema"
        )
    return "slsaprovenance02" if is_v02 else "slsaprovenance1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate BuildKit provenance and print its Cosign predicate type"
    )
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(validate_buildkit_provenance(args.input))
    except BuildKitProvenanceError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
