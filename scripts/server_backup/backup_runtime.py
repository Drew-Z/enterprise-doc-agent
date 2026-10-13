"""Single-owner backup ticks with durable upload resumption and honest freshness."""

import contextlib
import datetime
import hashlib
import json
import os
import secrets
import sqlite3
from pathlib import Path

MAX_SPOOL_BYTES = 128 * 1024 * 1024


class RuntimeErrorSafe(RuntimeError):
    """Operational state error without credentials or source contents."""


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


@contextlib.contextmanager
def runtime_lock(root):
    path = Path(root) / "runtime.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        acquired = False
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    pass
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def connect(root):
    database = sqlite3.connect(Path(root) / "runtime.sqlite", timeout=5)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA synchronous=FULL")
    database.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY, stage TEXT NOT NULL, created_at TEXT NOT NULL,
            captured_at TEXT, ciphertext_sha256 TEXT, ciphertext_bytes INTEGER,
            completed_at TEXT, last_error TEXT, publication TEXT
        );
    """)
    database.commit()
    return database


def read_status(root):
    with contextlib.closing(connect(root)) as db:
        latest = db.execute(
            "SELECT * FROM attempts ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        success = db.execute(
            "SELECT publication FROM attempts WHERE stage='succeeded' "
            "ORDER BY completed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return {
            "latest_attempt": dict(latest) if latest else None,
            "last_success": json.loads(success[0]) if success else None,
        }


def _spool_path(root, snapshot_id):
    path = root / "spool" / (snapshot_id + ".tar.age")
    if path.is_symlink() or path.resolve().parent != (root / "spool").resolve():
        raise RuntimeErrorSafe("spool path does not belong to runtime")
    return path


def run_tick(*, root, config_fingerprint, capture, seal, publish):
    root = Path(root).resolve()
    if not root.is_dir() or not isinstance(config_fingerprint, str) or not config_fingerprint:
        raise RuntimeErrorSafe("explicit runtime directory and binding are required")
    with runtime_lock(root) as acquired:
        if not acquired:
            return {"status": "busy"}
        spool = root / "spool"
        spool.mkdir(mode=0o700, exist_ok=True)
        if spool.is_symlink():
            raise RuntimeErrorSafe("spool directory must be owned by runtime")
        with contextlib.closing(connect(root)) as db:
            binding = db.execute("SELECT value FROM settings WHERE key='config'").fetchone()
            if binding and binding[0] != config_fingerprint:
                raise RuntimeErrorSafe("runtime target binding changed")
            if not binding:
                db.execute(
                    "INSERT INTO settings(key,value) VALUES('config',?)", (config_fingerprint,)
                )
                db.commit()
            # Publication was already durably recorded if a process ended while
            # cleaning its transport spool. Revalidate ownership and bytes first.
            for completed in db.execute(
                "SELECT id,ciphertext_sha256,ciphertext_bytes FROM attempts WHERE stage='succeeded'"
            ):
                leftover = _spool_path(root, completed["id"])
                if leftover.exists():
                    previous = leftover.read_bytes()
                    if (
                        len(previous) != completed["ciphertext_bytes"]
                        or hashlib.sha256(previous).hexdigest() != completed["ciphertext_sha256"]
                    ):
                        return {"status": "spool_integrity_failed", "snapshot_id": completed["id"]}
                    try:
                        leftover.unlink()
                    except OSError:
                        return {"status": "spool_cleanup_pending", "snapshot_id": completed["id"]}
            # The process lock proves there is no live earlier owner. Source-only
            # work may be abandoned; sealed/uploaded work must resume the same ID.
            db.execute(
                "UPDATE attempts SET stage='interrupted',last_error='process_ended_before_seal' "
                "WHERE stage='capturing'"
            )
            db.commit()
            pending = db.execute(
                "SELECT * FROM attempts WHERE stage='sealed' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if pending:
                snapshot_id = pending["id"]
                path = _spool_path(root, snapshot_id)
                try:
                    raw = path.read_bytes()
                    if (
                        len(raw) != pending["ciphertext_bytes"]
                        or hashlib.sha256(raw).hexdigest() != pending["ciphertext_sha256"]
                    ):
                        raise RuntimeErrorSafe("spool integrity differs")
                except (OSError, RuntimeErrorSafe):
                    db.execute(
                        "UPDATE attempts SET last_error='spool_integrity_failed' WHERE id=?",
                        (snapshot_id,),
                    )
                    db.commit()
                    return {"status": "spool_integrity_failed", "snapshot_id": snapshot_id}
                captured_at = pending["captured_at"]
            else:
                if (
                    sum(p.stat().st_size for p in spool.iterdir() if p.is_file())
                    > MAX_SPOOL_BYTES - 64 * 1024 * 1024
                ):
                    return {"status": "spool_budget_reached"}
                snapshot_id = (
                    "snapshot-"
                    + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
                    + "-"
                    + secrets.token_hex(6)
                )
                path = _spool_path(root, snapshot_id)
                db.execute(
                    "INSERT INTO attempts(id,stage,created_at) VALUES(?,'capturing',?)",
                    (snapshot_id, now()),
                )
                db.commit()
                try:
                    captured = capture()
                    captured_at = captured["captured_at"]
                    source_time = datetime.datetime.fromisoformat(captured_at)
                    if source_time.utcoffset() != datetime.timedelta(
                        0
                    ) or source_time > datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                        seconds=5
                    ):
                        raise RuntimeErrorSafe("invalid or future source time")
                    raw = seal(captured)
                    if not isinstance(raw, bytes) or not 32 <= len(raw) <= 64 * 1024 * 1024:
                        raise RuntimeErrorSafe("ciphertext exceeds spool budget")
                    with path.open("xb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                    digest = hashlib.sha256(raw).hexdigest()
                    db.execute(
                        "UPDATE attempts SET stage='sealed',captured_at=?,ciphertext_sha256=?,"
                        "ciphertext_bytes=? WHERE id=?",
                        (captured_at, digest, len(raw), snapshot_id),
                    )
                    db.commit()
                except Exception as exc:
                    db.execute(
                        "UPDATE attempts SET stage='capture_failed',last_error=? WHERE id=?",
                        (type(exc).__name__, snapshot_id),
                    )
                    db.commit()
                    return {
                        "status": "capture_failed",
                        "snapshot_id": snapshot_id,
                        "error_type": type(exc).__name__,
                    }
            try:
                receipt = publish(snapshot_id=snapshot_id, ciphertext=raw, captured_at=captured_at)
                if (
                    receipt.get("status") != "ciphertext_upload_readback_verified"
                    or receipt.get("snapshot_id") != snapshot_id
                    or receipt.get("captured_at") != captured_at
                    or receipt.get("ciphertext_sha256") != hashlib.sha256(raw).hexdigest()
                    or receipt.get("ciphertext_bytes") != len(raw)
                ):
                    raise RuntimeErrorSafe("publication receipt does not match captured bytes")
                db.execute(
                    "UPDATE attempts SET stage='succeeded',completed_at=?,last_error=NULL,"
                    "publication=? WHERE id=?",
                    (now(), json.dumps(receipt, sort_keys=True), snapshot_id),
                )
                db.commit()
            except Exception as exc:
                db.execute(
                    "UPDATE attempts SET last_error=? WHERE id=?", (type(exc).__name__, snapshot_id)
                )
                db.commit()
                return {
                    "status": "upload_pending",
                    "snapshot_id": snapshot_id,
                    "error_type": type(exc).__name__,
                }
            # Cleanup failure cannot turn an already verified publication into an
            # unknown upload. The next owner retries only the transport cleanup.
            cleanup_pending = False
            try:
                path.unlink()
            except OSError:
                cleanup_pending = True
            return {
                "status": "succeeded",
                "snapshot_id": snapshot_id,
                "captured_at": captured_at,
                "spool_cleanup_pending": cleanup_pending,
            }
