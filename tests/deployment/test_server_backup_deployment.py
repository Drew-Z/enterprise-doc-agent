"""Production package/configuration boundaries with real owned filesystem fixtures."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts.server_backup import deployment, host_environment, production_config


@pytest.fixture
def package(tmp_path):
    # Synthetic ELF header only: this fixture stages files and never executes age.
    binary = b"\x7fELF" + b"synthetic-test-binary"
    age = tmp_path / "age"
    age.write_bytes(binary)
    return deployment.prepare_package(
        source=Path(deployment.__file__).parent,
        age=age,
        expected_age_sha256=hashlib.sha256(binary).hexdigest(),
        public_recipient="age1" + "q" * 58,
        target_endpoint="https://" + "a" * 32 + ".r2.cloudflarestorage.com",
        target_bucket="synthetic-backup-test",
        release={"release": "v0.1.45-rc.16", "source": "0" * 40, "revision": "20260924_0031"},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("config_profile", "validation"),
        ("maximum_cycles", 3),
        ("validation_mode", True),
        ("interval_seconds", 1),
        ("interval_seconds", 60.5),
        ("remote_max_bytes", 3 * 1024**3 + 1),
        ("source_namespace", "other-tenant"),
        ("target_prefix", "unrelated/"),
        ("source_environment_command", ["sh", "-c", "unexpected"]),
        ("target_environment_command", ["cat", "/root/other-credentials"]),
        ("state_directory", "/tmp/state"),
        ("age_path", "/tmp/age"),
    ],
)
def test_production_configuration_rejects_scope_drift(package, field, value):
    config = copy.deepcopy(package["config"])
    config[field] = value
    with pytest.raises(production_config.ConfigurationError):
        production_config.validate_config(config)


def test_multipart_config_requires_matching_credential_command(package):
    config = copy.deepcopy(package["config"])
    assert "publication_multipart_enabled" not in config
    production_config.validate_config(config)
    config["publication_multipart_enabled"] = True
    with pytest.raises(production_config.ConfigurationError):
        production_config.validate_config(config)
    config["target_environment_command"].append("--multipart")
    production_config.validate_config(config)
    assert "server_backup/multipart_publication.py" in package["files"]
    for invalid in (False, 1, "true", None):
        config["publication_multipart_enabled"] = invalid
        with pytest.raises(production_config.ConfigurationError):
            production_config.validate_config(config)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:9000",
        "https://private@example.com",
        "https://" + "a" * 32 + ".r2.cloudflarestorage.com/other",
        "https://" + "a" * 32 + ".r2.cloudflarestorage.com?token=synthetic",
        "https://unrelated.example.test",
    ],
)
def test_target_endpoint_is_exact_https_r2(endpoint):
    with pytest.raises(production_config.ConfigurationError):
        production_config.validate_target(endpoint, "synthetic-backup-test")


def test_production_target_accepts_official_r2_hostname_and_rejects_previous_typo():
    production_config.validate_target(
        "https://" + "a" * 32 + ".r2.cloudflarestorage.com", "synthetic-backup-test"
    )
    with pytest.raises(production_config.ConfigurationError):
        production_config.validate_target(
            "https://" + "a" * 32 + ".r2.storage.cloudflare.com", "synthetic-backup-test"
        )


def test_tampered_package_or_age_binding_refuses_installation(package, tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    destination = tmp_path / "installation"
    changed = copy.deepcopy(package)
    changed["files"]["server_backup/backup_runtime.py"] += b"\n# changed"
    with pytest.raises(deployment.DeploymentError):
        deployment.install_first(
            changed,
            root=destination,
            unit_directory=unit_dir,
            runner=lambda *args, **kwargs: pytest.fail("must not run"),
        )
    assert not destination.exists()
    changed = copy.deepcopy(package)
    changed["config"]["age_sha256"] = "0" * 64
    with pytest.raises(deployment.DeploymentError):
        deployment.verify_package(changed)


def test_first_installation_is_exclusive_and_never_starts_backup(package, tmp_path, monkeypatch):
    monkeypatch.setattr(deployment.os, "geteuid", lambda: 0, raising=False)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    root = tmp_path / "installation"
    calls = []

    def run(args, **kwargs):
        calls.append(args)

    result = deployment.install_first(package, root=root, unit_directory=unit_dir, runner=run)
    assert result["status"] == "staged_not_started"
    assert not result["credential_file_created"]
    assert not (root / "target.json").exists()
    assert calls == [["systemctl", "daemon-reload"]]
    unit = (unit_dir / deployment.UNIT).read_text()
    assert "--require-production-config" in unit
    assert "native_backup_probe" not in unit
    assert "ReadWritePaths=/var/lib/enterprise-doc-backup/state" in unit
    for name, entry in package["inventory"].items():
        data = (root / "releases" / package["package_id"] / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    before = (root / "installation.json").read_bytes()
    with pytest.raises(deployment.DeploymentError):
        deployment.install_first(package, root=root, unit_directory=unit_dir, runner=run)
    assert (root / "installation.json").read_bytes() == before
    stopped = deployment.stop_owned_installation(result, runner=run)
    assert stopped["deleted_files"] == 0
    assert calls[-1] == ["systemctl", "disable", "--now", deployment.UNIT]
    assert (root / "installation.json").read_bytes() == before


def test_existing_unit_is_preserved_before_creating_root(package, tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    unit = unit_dir / deployment.UNIT
    unit.write_text("existing unit")
    root = tmp_path / "installation"
    with pytest.raises(deployment.DeploymentError):
        deployment.install_first(
            package,
            root=root,
            unit_directory=unit_dir,
            runner=lambda *args, **kwargs: pytest.fail("must not run"),
        )
    assert unit.read_text() == "existing unit"
    assert not root.exists()


def test_rollback_refuses_a_modified_unit(package, tmp_path, monkeypatch):
    monkeypatch.setattr(deployment.os, "geteuid", lambda: 0, raising=False)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    receipt = deployment.install_first(
        package,
        root=tmp_path / "installation",
        unit_directory=unit_dir,
        runner=lambda *args, **kwargs: None,
    )
    (unit_dir / deployment.UNIT).write_text("changed unit")
    with pytest.raises(deployment.DeploymentError):
        deployment.stop_owned_installation(
            receipt, runner=lambda *args, **kwargs: pytest.fail("must not stop changed service")
        )


def test_source_adapter_reads_only_selected_environment_in_memory(monkeypatch):
    source = {name: "synthetic-" + name for name in host_environment.SOURCE_FIELDS}
    commands = []

    def boundary(command, **kwargs):
        commands.append(command)
        assert kwargs["max_bytes"] == 65536
        return json.dumps(source).encode()

    monkeypatch.setattr(host_environment, "limited_output", boundary)
    result = host_environment.source_environment(production_config.NAMESPACE)
    assert result["database_url"] == source["DATABASE__URL"]
    assert result["object_secret"] == source["OBJECT_STORE__SECRET_KEY"]
    assert commands[0][:5] == [
        "/usr/local/bin/k3s",
        "kubectl",
        "-n",
        production_config.NAMESPACE,
        "exec",
    ]
    assert not any(value in " ".join(commands[0]) for value in source.values())
    with pytest.raises(production_config.ConfigurationError):
        host_environment.source_environment("another-namespace")
    assert len(commands) == 1


def test_target_adapter_rejects_other_files_and_extra_admin_credentials(monkeypatch, package):
    value = {
        "endpoint": package["config"]["target_endpoint"],
        "bucket": package["config"]["target_bucket"],
        "access": "b" * 32,
        "secret": "c" * 64,
        "region": "auto",
    }
    monkeypatch.setattr(host_environment, "protected_json", lambda path: value)
    result = host_environment.target_environment(production_config.ROOT + "/target.json")
    assert result["access"] == value["access"]
    assert result["secret"] != value["secret"]
    assert result["session_token"]
    with pytest.raises(production_config.ConfigurationError):
        host_environment.target_environment("/tmp/other.json")
    value["cloudflare_admin_token"] = "synthetic-extra"
    with pytest.raises(production_config.ConfigurationError):
        host_environment.target_environment(production_config.ROOT + "/target.json")
