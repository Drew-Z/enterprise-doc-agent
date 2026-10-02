"""Prepare and stage a first installation without credentials or implicit startup.

Staging never overwrites an existing root or systemd unit. Rollback stops only the
hash-verified owned unit and retains every source, credential, state and backup.
"""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from .production_config import NAMESPACE, ROOT, validate_config

UNIT = "enterprise-doc-backup.service"
MODULES = (
    "__init__.py",
    "backup_runtime.py",
    "backup_daemon.py",
    "server_capture.py",
    "server_publication.py",
    "remote_retention.py",
    "restore_catalog.py",
    "recovery_bundle.py",
    "database_environment.py",
    "production_config.py",
    "host_environment.py",
    "deployment.py",
)


class DeploymentError(RuntimeError):
    """Bounded operational failure without configuration or credential contents."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def prepare_package(
    *,
    source,
    age,
    expected_age_sha256,
    public_recipient,
    target_endpoint,
    target_bucket,
    release,
    extra_artifact_ids=(),
):
    source, age = Path(source), Path(age)
    if source.is_symlink() or age.is_symlink():
        raise DeploymentError("package sources must not be symlinks")
    files = {}
    for name in MODULES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024:
            raise DeploymentError("package module inventory differs")
        files["server_backup/" + name] = path.read_bytes()
    if not age.is_file() or not 1 <= age.stat().st_size <= 32 * 1024 * 1024:
        raise DeploymentError("age binary is missing or exceeds package budget")
    binary = age.read_bytes()
    if digest(binary) != expected_age_sha256 or not binary.startswith(b"\x7fELF"):
        # Linux age is a pinned ELF binary. Other executables cannot be staged.
        raise DeploymentError("pinned Linux age binary differs")
    files["age"] = binary
    inventory = {
        name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in sorted(files.items())
    }
    package_id = digest(json_bytes(inventory))
    root = Path(ROOT)
    command = ["/usr/bin/python3", "-B", "-m", "server_backup.host_environment"]
    config = {
        "schema_version": 1,
        "config_profile": "production",
        "installation_root": ROOT,
        "package_id": package_id,
        "state_directory": str(root / "state"),
        "age_path": str(root / "releases" / package_id / "age"),
        "age_sha256": expected_age_sha256,
        "public_recipient": public_recipient,
        "source_namespace": NAMESPACE,
        "source_environment_command": [*command, "source", "--namespace", NAMESPACE],
        "target_environment_command": [*command, "target", "--file", str(root / "target.json")],
        "target_endpoint": target_endpoint,
        "target_bucket": target_bucket,
        "target_prefix": "operations-recovery/v1/",
        "remote_max_bytes": 3 * 1024**3,
        "interval_seconds": 60,
        "release": release,
        "extra_artifact_ids": list(extra_artifact_ids),
    }
    # Use Linux paths even if the package is prepared on Windows.
    for key in ("state_directory", "age_path"):
        config[key] = config[key].replace("\\", "/")
    config["target_environment_command"][-1] = ROOT + "/target.json"
    validate_config(config)
    return {"package_id": package_id, "inventory": inventory, "files": files, "config": config}


def render_unit(package_id):
    if not isinstance(package_id, str) or len(package_id) != 64:
        raise DeploymentError("invalid package identity")
    if any(char not in "0123456789abcdef" for char in package_id):
        raise DeploymentError("invalid package identity")
    return f"""[Unit]
Description=Consistent encrypted DocAgent backup
After=network-online.target k3s.service
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=notify
NotifyAccess=main
User=root
UMask=0077
WorkingDirectory={ROOT}/releases/{package_id}
ExecStart=/usr/bin/python3 -B -m server_backup.backup_daemon \\
    --require-production-config --config {ROOT}/config.json
Restart=on-failure
RestartSec=15
WatchdogSec=600
TimeoutStopSec=30
KillMode=control-group
MemoryMax=640M
CPUQuota=150%
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={ROOT}/state
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def verify_package(package):
    expected = {"server_backup/" + name for name in MODULES} | {"age"}
    if set(package["files"]) != expected or set(package["inventory"]) != expected:
        raise DeploymentError("package member inventory differs")
    for name, raw in package["files"].items():
        record = package["inventory"][name]
        maximum = 32 * 1024 * 1024 if name == "age" else 128 * 1024
        if (
            not isinstance(raw, bytes)
            or not 1 <= len(raw) <= maximum
            or record != {"sha256": digest(raw), "bytes": len(raw)}
        ):
            raise DeploymentError("package member integrity differs")
    if digest(json_bytes(package["inventory"])) != package["package_id"]:
        raise DeploymentError("package identity differs")
    if package["config"]["package_id"] != package["package_id"]:
        raise DeploymentError("config package identity differs")
    if package["config"]["age_sha256"] != package["inventory"]["age"]["sha256"] or not package[
        "files"
    ]["age"].startswith(b"\x7fELF"):
        raise DeploymentError("config age binding differs")
    validate_config(package["config"])


def install_first(package, *, root=None, unit_directory=None, runner=subprocess.run):
    """Stage files only; root/unit overrides support owned filesystem test fixtures."""
    verify_package(package)
    root = Path(ROOT) if root is None else Path(root)
    unit_directory = Path("/etc/systemd/system") if unit_directory is None else Path(unit_directory)
    unit_path = unit_directory / UNIT
    if (
        str(root) != ROOT or str(unit_directory) != "/etc/systemd/system"
    ) and runner is subprocess.run:
        raise DeploymentError("non-production paths require an explicit test command boundary")
    if not root.is_absolute() or not unit_directory.is_absolute():
        raise DeploymentError("installation paths must be absolute")
    if any(p.is_symlink() for path in (root, unit_directory) for p in [path, *path.parents]):
        raise DeploymentError("installation path contains a symlink")
    if (
        root.exists()
        or unit_path.exists()
        or not root.parent.is_dir()
        or not unit_directory.is_dir()
    ):
        raise DeploymentError("installation target already exists or parent is absent")
    if os.name == "posix" and os.geteuid() != 0:
        raise DeploymentError("native service installation requires root")
    unit = render_unit(package["package_id"]).encode()
    root.mkdir(mode=0o700)
    release = root / "releases" / package["package_id"]
    (release / "server_backup").mkdir(parents=True, mode=0o700)
    (root / "state").mkdir(mode=0o700)
    for name, raw in package["files"].items():
        destination = release / name
        with destination.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o700 if name == "age" else 0o600)
    with (root / "config.json").open("xb") as output:
        output.write(json_bytes(package["config"]))
    (root / "config.json").chmod(0o600)
    receipt = {
        "status": "staged_not_started",
        "package_id": package["package_id"],
        "root": str(root),
        "unit": str(unit_path),
        "unit_sha256": digest(unit),
        "config_sha256": digest(json_bytes(package["config"])),
        "inventory": package["inventory"],
        "credential_file_created": False,
        "backup_started": False,
    }
    with (root / "installation.json").open("xb") as output:
        output.write(json_bytes(receipt))
    (root / "installation.json").chmod(0o600)
    # Exclusive unit creation is the final race check. Failed staging is retained.
    with unit_path.open("xb") as output:
        output.write(unit)
    unit_path.chmod(0o600)
    runner(["systemctl", "daemon-reload"], check=True, capture_output=True, timeout=30)
    return receipt


def stop_owned_installation(receipt, *, runner=subprocess.run):
    path = Path(receipt["unit"])
    if path.name != UNIT or path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise DeploymentError("owned unit path differs")
    if digest(path.read_bytes()) != receipt["unit_sha256"]:
        raise DeploymentError("owned unit changed; rollback refused")
    runner(["systemctl", "disable", "--now", UNIT], check=True, capture_output=True, timeout=45)
    return {"status": "owned_service_stopped_files_retained", "deleted_files": 0}
