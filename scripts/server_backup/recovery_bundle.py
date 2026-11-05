"""Bounded in-memory tar bundles authenticated by the pinned age executable.

No plaintext archives or decryption identities are written by this module.
The caller owns key storage, binary verification and snapshot consistency.
"""

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
MANIFEST = "bundle.json"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BundleError(RuntimeError):
    """Safe failure without payload, identity, source keys or provider errors."""


def _name(name):
    return (
        isinstance(name, str)
        and 0 < len(name) <= 100
        and re.fullmatch(r"[a-zA-Z0-9_./-]+", name) is not None
        and not name.startswith("/")
        and not name.endswith("/")
        and all(p not in ("", ".", "..") for p in name.split("/"))
    )


def _age(age, args, data):
    if len(data) > MAX_BYTES:
        raise BundleError("bundle exceeds memory budget")
    try:
        result = subprocess.run(
            [str(age), *args], input=data, capture_output=True, timeout=60, creationflags=FLAGS
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BundleError("age process could not complete") from None
    # Never parse or expose stdout until age authenticates the entire ciphertext.
    if result.returncode != 0 or len(result.stdout) > MAX_BYTES:
        raise BundleError("age authentication or encryption failed")
    return result.stdout


def seal_bundle(*, age, recipient, payload, metadata):
    if not re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", recipient):
        raise BundleError("expected one native age public recipient")
    if not isinstance(payload, dict) or not 1 <= len(payload) <= MAX_FILES:
        raise BundleError("invalid bundle file inventory")
    total = 0
    entries = []
    for name, data in sorted(payload.items()):
        if name == MANIFEST or not _name(name) or not isinstance(data, bytes):
            raise BundleError("invalid bundle member")
        total += len(data)
        if total > MAX_BYTES - 8 * 1024 * 1024:
            raise BundleError("bundle exceeds payload budget")
        entries.append(
            {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    try:
        manifest = json.dumps(
            {"version": 1, "metadata": metadata, "files": entries},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError):
        raise BundleError("invalid bundle metadata") from None
    if len(manifest) > 1024 * 1024:
        raise BundleError("bundle manifest exceeds budget")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [(MANIFEST, manifest), *sorted(payload.items())]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            member.mtime = 0
            archive.addfile(member, io.BytesIO(data))
    return _age(age, ["--encrypt", "--recipient", recipient], output.getvalue())


def open_bundle(*, age, identity, ciphertext):
    identity = Path(identity)
    if not identity.is_file() or identity.is_symlink():
        raise BundleError("local identity is unavailable")
    plaintext = _age(age, ["--decrypt", "--identity", str(identity)], ciphertext)
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            total = 0
            for member in archive:
                if (
                    not member.isfile()
                    or not _name(member.name)
                    or member.name in files
                    or len(files) > MAX_FILES
                    or member.size < 0
                ):
                    raise BundleError("invalid decrypted archive member")
                total += member.size
                if total > MAX_BYTES:
                    raise BundleError("decrypted archive exceeds budget")
                handle = archive.extractfile(member)
                data = handle.read(member.size + 1)
                if len(data) != member.size:
                    raise BundleError("decrypted archive is truncated")
                files[member.name] = data
        raw = files.pop(MANIFEST)
        if len(raw) > 1024 * 1024:
            raise BundleError("decrypted manifest exceeds budget")
        manifest = json.loads(raw)
        if manifest["version"] != 1 or not 1 <= len(manifest["files"]) <= MAX_FILES:
            raise BundleError("unsupported bundle manifest")
        expected = {}
        for item in manifest["files"]:
            name = item["name"]
            if name in expected or name == MANIFEST or not _name(name):
                raise BundleError("invalid manifest file inventory")
            expected[name] = item
        if set(expected) != set(files):
            raise BundleError("bundle inventory differs from manifest")
        for name, data in files.items():
            item = expected[name]
            if (
                type(item["size"]) is not int
                or len(data) != item["size"]
                or hashlib.sha256(data).hexdigest() != item["sha256"]
            ):
                raise BundleError("bundle component failed integrity verification")
        return {"metadata": manifest["metadata"], "payload": files}
    except BundleError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, tarfile.TarError, OSError):
        raise BundleError("decrypted bundle could not be verified") from None
