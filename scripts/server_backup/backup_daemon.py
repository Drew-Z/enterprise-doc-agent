"""Linux service entrypoint: capture, encrypt and publish with durable state."""

import argparse
import datetime
import hashlib
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path

import boto3
from botocore.config import Config

from .backup_runtime import run_tick
from .database_environment import postgres_process_environment
from .production_config import protected_json, validate_config
from .recovery_bundle import seal_bundle
from .remote_retention import DEFAULT_MAX_BYTES, publish_budgeted_snapshot
from .server_capture import (
    SOURCE_READ_CONCURRENCY,
    capture_database,
    capture_objects,
    limited_output,
)
from .target_credentials import validate_session


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.sendto(message.encode(), address)


def environment(command):
    raw = limited_output(command, env=os.environ.copy(), timeout=20, max_bytes=128 * 1024)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("runtime credential source did not return an object")
    return value


def storage(
    endpoint,
    access,
    secret,
    region="auto",
    *,
    session_token=None,
    max_pool_connections=4,
    congestion_control=None,
):
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        aws_session_token=session_token,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=10,
            retries={"total_max_attempts": 1},
            max_pool_connections=max_pool_connections,
        ),
    )
    if congestion_control is not None:
        try:
            if congestion_control != "bbr":
                raise ValueError("unsupported backup congestion control")
            # Probe the installed kernel implementation before making a request.
            # This changes only these sockets, never the host's default algorithm.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, b"bbr")
                selected = probe.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, 16)
                if selected.split(b"\0")[0] != b"bbr":
                    raise ValueError("backup congestion control was not applied")
            # Botocore does not expose TCP_CONGESTION through Config. Preserve
            # its TLS, timeout and existing socket options on the native manager.
            manager = client._endpoint.http_session._manager
            manager.connection_pool_kw["socket_options"] = [
                *manager.connection_pool_kw["socket_options"],
                (socket.IPPROTO_TCP, socket.TCP_CONGESTION, b"bbr"),
            ]
        except Exception:
            client.close()
            raise
    return client


class BackupService:
    def __init__(self, config_path, *, require_production=False):
        if require_production:
            self.config = validate_config(protected_json(config_path))
            raw = json.dumps(self.config, sort_keys=True, separators=(",", ":")).encode()
        else:
            raw = Path(config_path).read_bytes()
            self.config = json.loads(raw)
        self.binding = hashlib.sha256(raw).hexdigest()
        self.root = Path(self.config["state_directory"]).resolve()
        self.cache = {}
        if not self.root.is_dir():
            raise ValueError("runtime directory must be provisioned explicitly")
        age = Path(self.config["age_path"])
        if hashlib.sha256(age.read_bytes()).hexdigest() != self.config["age_sha256"]:
            raise ValueError("pinned age binary differs")
        interval = self.config["interval_seconds"]
        if self.config.get("validation_mode"):
            if (
                Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                != self.config["expected_boot_id"]
                or self.config["source_namespace"] != "docagent-recovery"
            ):
                raise ValueError("local validation guest binding differs")
            if not 1 <= interval <= 300:
                raise ValueError("invalid validation interval")
        elif not 60 <= interval <= 300:
            raise ValueError("invalid server capture interval")

    def capture(self):
        cfg = self.config
        source = environment(cfg["source_environment_command"])
        env = postgres_process_environment(source["database_url"])
        env.update(
            PGCONNECT_TIMEOUT="10",
            PGAPPNAME="docagent-continuous-backup",
            PGOPTIONS=(
                "-c default_transaction_read_only=on -c statement_timeout=180000 "
                "-c idle_in_transaction_session_timeout=240000"
            ),
        )
        started = time.monotonic()
        database = capture_database(
            psql=cfg.get("psql_command", ["psql"]),
            pg_dump=cfg.get("pg_dump_command", ["pg_dump"]),
            env=env,
            extra_artifact_ids=tuple(cfg.get("extra_artifact_ids", [])),
        )
        client = storage(
            source["object_endpoint"],
            source["object_access"],
            source["object_secret"],
            source.get("object_region", "auto"),
            max_pool_connections=SOURCE_READ_CONCURRENCY,
        )
        try:
            objects = capture_objects(
                client=client,
                references=database["references"],
                documents_bucket=source["documents_bucket"],
                allowed_buckets={source["documents_bucket"], source["artifacts_bucket"]},
                cache=self.cache,
            )
        finally:
            client.close()
        payload = objects["payload"]
        payload["database.dump"] = database["database"]
        payload["source-inventory.json"] = json.dumps(
            database["inventory"], sort_keys=True
        ).encode()
        payload["release.json"] = json.dumps(cfg["release"], sort_keys=True).encode()
        result = {
            "captured_at": database["inventory"]["captured_at"],
            "payload": payload,
            "database_sha256": database["inventory"]["dump_sha256"],
        }
        print(
            json.dumps(
                {
                    "event": "capture_complete",
                    "at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "captured_at": result["captured_at"],
                    "tables": len(database["inventory"]["inventory"]),
                    "rows": sum(t["rows"] for t in database["inventory"]["inventory"]),
                    "objects": objects["objects"],
                    "object_source_gets": objects["source_gets"],
                    "elapsed_seconds": time.monotonic() - started,
                }
            ),
            flush=True,
        )
        return result

    def seal(self, capture):
        return seal_bundle(
            age=Path(self.config["age_path"]),
            recipient=self.config["public_recipient"],
            payload=capture["payload"],
            metadata={
                "captured_at": capture["captured_at"],
                "database_sha256": capture["database_sha256"],
            },
        )

    def publish(self, **kwargs):
        multipart = self.config.get("publication_multipart_enabled", False)
        if type(multipart) is not bool:
            raise ValueError("invalid publication transport option")
        target = environment(self.config["target_environment_command"])
        if self.config.get("config_profile") == "production":
            validate_session(target, multipart=multipart)
        if (
            target["endpoint"] != self.config["target_endpoint"]
            or target["bucket"] != self.config["target_bucket"]
        ):
            raise ValueError("backup target changed")
        client = storage(
            target["endpoint"],
            target["access"],
            target["secret"],
            target.get("region", "auto"),
            session_token=target.get("session_token"),
            congestion_control="bbr" if self.config.get("config_profile") == "production" else None,
        )
        try:
            return publish_budgeted_snapshot(
                client=client,
                bucket=target["bucket"],
                prefix=self.config["target_prefix"],
                max_bytes=self.config.get("remote_max_bytes", DEFAULT_MAX_BYTES),
                multipart_journal=self.root / "runtime.sqlite" if multipart else None,
                **kwargs,
            )
        finally:
            client.close()

    def tick(self):
        return run_tick(
            root=self.root,
            config_fingerprint=self.binding,
            capture=self.capture,
            seal=self.seal,
            publish=self.publish,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--require-production-config", action="store_true")
    args = parser.parse_args()
    service = BackupService(args.config, require_production=args.require_production_config)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    notify("READY=1\nSTATUS=Backup capture service initialized")
    cycles = 0
    while not stopping.is_set():
        started = time.monotonic()
        try:
            result = service.tick()
        except Exception as exc:
            result = {"status": "runtime_failed", "error_type": type(exc).__name__}
        print(
            json.dumps(
                {
                    "event": "backup_tick",
                    "at": datetime.datetime.now(datetime.UTC).isoformat(),
                    **result,
                }
            ),
            flush=True,
        )
        notify("WATCHDOG=1\nSTATUS=Backup tick " + result["status"])
        cycles += 1
        maximum = service.config.get("maximum_cycles")
        if maximum is not None and cycles >= maximum:
            break
        remaining = max(1, service.config["interval_seconds"] - (time.monotonic() - started))
        while remaining > 0 and not stopping.is_set():
            wait = min(20, remaining)
            stopping.wait(wait)
            remaining -= wait
            notify("WATCHDOG=1")
    notify("STOPPING=1")


if __name__ == "__main__":
    main()
