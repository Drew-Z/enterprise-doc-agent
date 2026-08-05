from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.recovery.object_store import ObjectReference, load_object_references

MAX_MANIFEST_BYTES = 64 * 1024 * 1024


async def load_settings_references(
    settings: FoundationSettings,
) -> tuple[ObjectReference, ...]:
    engine = create_database_engine(settings.database)
    try:
        return await load_object_references(
            create_session_factory(engine),
            documents_bucket=settings.object_store.documents_bucket,
        )
    finally:
        await engine.dispose()


def run_reference_load(settings: FoundationSettings) -> tuple[ObjectReference, ...]:
    return asyncio.run(load_settings_references(settings))


def read_manifest(path: Path) -> bytes:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("snapshot manifest exceeds the maximum supported size")
    return path.read_bytes()


def write_private_bytes(path: Path, body: bytes, *, immutable: bool) -> None:
    if path.exists() and immutable:
        if path.read_bytes() != body:
            raise FileExistsError("private recovery artifact already exists with different content")
        path.chmod(0o600)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_private_record(path: Path, record: dict[str, object]) -> None:
    body = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    write_private_bytes(path, body, immutable=False)
