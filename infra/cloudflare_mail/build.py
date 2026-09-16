"""Build the pinned mailbox locally, without provisioning any Cloudflare resource."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import TypedDict, cast
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PNPM_VERSION = "10.10.0"
NL = chr(10)


class SourceFile(TypedDict):
    path: str
    bytes: int
    sha256: str
    git_blob_sha1: str


class SourceLock(TypedDict):
    repository: str
    release: str
    commit: str
    archive_url: str
    archive_sha256: str
    archive_bytes: int
    files: list[SourceFile]


def sha256(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_lock() -> SourceLock:
    return cast(SourceLock, json.loads((HERE / "source.lock.json").read_text("utf-8")))


def verify_source(source: Path, lock: SourceLock) -> None:
    """Reject missing, altered, linked or additional selected source files."""
    expected = {item["path"] for item in lock["files"]}
    actual: set[str] = set()
    for root_name in ("worker", "frontend", "db", "LICENSE"):
        root = source / root_name
        if root.is_symlink() or root.is_junction():
            raise ValueError("Linked source roots are not allowed")
        paths = root.rglob("*") if root.is_dir() else iter([root])
        for file in paths:
            if file.is_symlink() or file.is_junction():
                raise ValueError("Linked source entries are not allowed")
            if file.is_file():
                actual.add(file.relative_to(source).as_posix())
    if actual != expected:
        raise ValueError("Source file set does not match the pinned release")
    for item in lock["files"]:
        file = source / item["path"]
        if file.stat().st_size != item["bytes"] or sha256(file) != item["sha256"]:
            raise ValueError(f"Pinned source mismatch: {item['path']}")


def prepare_source(work: Path, supplied: Path | None, lock: SourceLock) -> Path:
    source = work / "upstream"
    source.mkdir()
    if supplied is not None:
        verify_source(supplied, lock)
        for item in lock["files"]:
            target = source / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(supplied / item["path"], target)
    else:
        archive_path = work / "upstream.tar.gz"
        request = Request(lock["archive_url"], headers={"User-Agent": "docagent-mail-build"})
        with urlopen(request, timeout=60) as response, archive_path.open("xb") as output:
            count = 0
            while chunk := response.read(64 * 1024):
                count += len(chunk)
                if count > lock["archive_bytes"]:
                    raise ValueError("Source archive exceeds its pinned size")
                output.write(chunk)
        if count != lock["archive_bytes"] or sha256(archive_path) != lock["archive_sha256"]:
            raise ValueError("Source archive does not match its pinned digest")
        expected = {item["path"]: item for item in lock["files"]}
        seen: set[str] = set()
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                relative = PurePosixPath(*PurePosixPath(member.name).parts[1:]).as_posix()
                if relative not in expected:
                    continue
                if not member.isfile() or relative in seen:
                    raise ValueError("Unexpected source archive entry")
                if member.size != expected[relative]["bytes"]:
                    raise ValueError("Source member size differs from lock")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("Missing source member")
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                seen.add(relative)
        if seen != expected.keys():
            raise ValueError("Incomplete source archive")
    verify_source(source, lock)
    return source


def local_environment(work: Path, cache: Path | None = None) -> dict[str, str]:
    """Build tools receive OS settings, not Cloudflare/product/provider credentials."""
    keep = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "HOME",
        "SYSTEMDRIVE",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "HOMEDRIVE",
        "HOMEPATH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    cache = cache or work
    empty_npmrc = work / "empty.npmrc"
    empty_npmrc.write_text("", encoding="utf-8")
    env.update(
        {
            "CI": "1",
            "NO_COLOR": "1",
            "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
            "COREPACK_ENABLE_AUTO_PIN": "0",
            "COREPACK_HOME": str(cache / "corepack"),
            "PNPM_HOME": str(work / "pnpm"),
            "WRANGLER_SEND_METRICS": "false",
            "WRANGLER_LOG_PATH": str(work / "wrangler-logs"),
            "CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV": "false",
            "NPM_CONFIG_USERCONFIG": str(empty_npmrc),
            "NPM_CONFIG_GLOBALCONFIG": str(empty_npmrc),
            "VITE_API_BASE": "",
            "VITE_DEFAULT_LANG": "zh",
            "VITE_CF_WEB_ANALY_TOKEN": "",
            "VITE_IS_TELEGRAM": "false",
            "VITE_PWA_DISABLED": "true",
        }
    )
    return env


def toolchain() -> tuple[str, Path]:
    node = shutil.which("node")
    corepack = shutil.which("corepack")
    if not node or not corepack:
        raise ValueError("Node >=22 and Corepack are required")
    located = Path(corepack)
    candidates = [
        located.parent / "node_modules/corepack/dist/pnpm.js",
        located.resolve().with_name("pnpm.js"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return node, candidate
    raise ValueError("Corepack pnpm.js entry point could not be resolved")


def prepare_private_frontend(frontend: Path) -> dict[str, str]:
    """Remove the unused remote captcha script only in the verified build copy."""
    index = frontend / "index.html"
    original_hash = sha256(index)
    html = index.read_bytes()
    script = (
        b'<script src="https://challenges.cloudflare.com/turnstile/v0/api.js'
        b'?render=explicit"></script>'
    )
    if html.count(script) != 1:
        raise ValueError("Expected one pinned Turnstile script in the frontend entry")
    index.write_bytes(html.replace(script, b""))
    return {
        "path": "frontend/index.html",
        "reason": "Remove unconditional Turnstile request; this private package disables captcha",
        "source_sha256": original_hash,
        "build_copy_sha256": sha256(index),
    }


def run_step(
    name: str, command: list[str], cwd: Path, env: dict[str, str], logs: Path, *, timeout: int = 600
) -> str:
    print(f"[{name}] running", flush=True)
    started = time.monotonic()
    with (logs / f"{name}.log").open("w", encoding="utf-8") as output:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}); see its local build log")
    print(f"[{name}] passed ({time.monotonic() - started:.1f}s)", flush=True)
    return (logs / f"{name}.log").read_text("utf-8")


def build(
    source_dir: Path | None, work: Path, output: Path, *, cache: Path | None = None
) -> dict[str, object]:
    if work.exists() or output.exists():
        raise ValueError("Work and output directories must both be new; nothing was overwritten")
    if output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError("Work and output directories must be separate")
    lock = read_lock()
    if source_dir is not None:
        verify_source(source_dir, lock)
    node, corepack = toolchain()
    work.mkdir(parents=True)
    logs = work / "logs"
    logs.mkdir()
    source = prepare_source(work, source_dir, lock)
    cache = cache or work
    cache.mkdir(parents=True, exist_ok=True)
    env = local_environment(work, cache)
    worker, frontend = source / "worker", source / "frontend"
    pnpm = [node, str(corepack)]
    node_version = run_step("node-version", [node, "--version"], worker, env, logs).strip()
    if int(node_version.lstrip("v").split(".")[0]) < 22:
        raise ValueError("Node >=22 is required by the pinned Wrangler")
    pnpm_version = run_step("pnpm-version", [*pnpm, "--version"], worker, env, logs).strip()
    if pnpm_version != PNPM_VERSION:
        raise ValueError("Package-manager version does not match the upstream pin")
    for label, package in (("worker", worker), ("frontend", frontend)):
        run_step(
            label + "-install",
            [
                *pnpm,
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
                "--reporter",
                "append-only",
                "--store-dir",
                str(cache / "pnpm-store"),
            ],
            package,
            env,
            logs,
        )
    frontend_override = prepare_private_frontend(frontend)
    run_step(
        "frontend-build", [*pnpm, "exec", "vite", "build", "--mode", "pages"], frontend, env, logs
    )
    config = json.loads((HERE / "wrangler.jsonc").read_text("utf-8"))
    config["main"] = "private-entry.mjs"
    config["$schema"] = str(worker / "node_modules/wrangler/config-schema.json")
    config["assets"]["directory"] = str(frontend / "dist")
    config_path = worker / "wrangler.package.jsonc"
    config_path.write_text(json.dumps(config, indent=2) + NL, encoding="utf-8")
    for name in ("private-entry.mjs", "private-handler.mjs"):
        shutil.copyfile(HERE / name, worker / name)
    wrangler = [*pnpm, "exec", "wrangler"]
    wrangler_version = run_step(
        "wrangler-version", [*wrangler, "--version"], worker, env, logs
    ).strip()
    run_step(
        "worker-build",
        [
            *wrangler,
            "deploy",
            "--dry-run",
            "--config",
            str(config_path),
            "--outdir",
            str(work / "compiled"),
            "--minify",
        ],
        worker,
        env,
        logs,
    )
    run_step(
        "worker-types",
        [
            *wrangler,
            "types",
            str(worker / "worker-configuration.d.ts"),
            "--config",
            str(config_path),
        ],
        worker,
        env,
        logs,
    )
    # Upstream locks TypeScript transitively; pnpm exec tsc has no root command shim.
    compilers = list(
        (worker / "node_modules/.pnpm").glob("typescript@*/node_modules/typescript/bin/tsc")
    )
    if len(compilers) != 1:
        raise ValueError("Expected one TypeScript compiler from the frozen dependency graph")
    run_step(
        "private-handler-types",
        [
            node,
            str(compilers[0]),
            "--allowJs",
            "--checkJs",
            "--noEmit",
            "--strict",
            "--skipLibCheck",
            "--target",
            "ES2022",
            "--module",
            "ESNext",
            "--moduleResolution",
            "bundler",
            "--types",
            "@cloudflare/workers-types",
            "private-handler.mjs",
            "worker-configuration.d.ts",
        ],
        worker,
        env,
        logs,
    )
    for label, package in (("worker", worker), ("frontend", frontend)):
        expected = next(
            item["sha256"] for item in lock["files"] if item["path"] == label + "/pnpm-lock.yaml"
        )
        if sha256(package / "pnpm-lock.yaml") != expected:
            raise ValueError("Frozen dependency lock was modified")
    output.mkdir(parents=True)
    for file in (work / "compiled").iterdir():
        if file.is_file() and file.suffix in {".js", ".wasm"}:
            target_name = "worker.js" if file.name == "private-entry.js" else file.name
            shutil.copyfile(file, output / target_name)
    if not (output / "worker.js").is_file():
        raise ValueError("Expected compiled Worker entry was not produced")
    shutil.copytree(frontend / "dist", output / "assets")
    for name in ("wrangler.jsonc", "private-settings.sql", "README.md"):
        shutil.copyfile(HERE / name, output / name)
    shutil.copyfile(source / "db/schema.sql", output / "schema.sql")
    shutil.copyfile(source / "LICENSE", output / "LICENSE.upstream")
    provenance = output / "provenance"
    provenance.mkdir()
    shutil.copyfile(
        worker / "node_modules/wrangler/config-schema.json",
        provenance / "wrangler-config-schema.json",
    )
    shutil.copyfile(HERE / "source.lock.json", provenance / "source.lock.json")
    for label, package in (("worker", worker), ("frontend", frontend)):
        shutil.copyfile(package / "pnpm-lock.yaml", provenance / f"{label}-pnpm-lock.yaml")
    files = [
        {
            "path": file.relative_to(output).as_posix(),
            "bytes": file.stat().st_size,
            "sha256": sha256(file),
        }
        for file in sorted(output.rglob("*"))
        if file.is_file()
    ]
    manifest: dict[str, object] = {
        "source_commit": lock["commit"],
        "source_release": lock["release"],
        "node_version": node_version,
        "pnpm_version": pnpm_version,
        "wrangler_version": wrangler_version,
        "dependency_install_scripts_enabled": False,
        "receive_only": True,
        "public_routes_enabled": False,
        "real_email_sent": False,
        "cloudflare_resources_created": False,
        "build_copy_overrides": [frontend_override],
        "package_inputs": {
            name: sha256(HERE / name)
            for name in (
                "build.py",
                "private-handler.mjs",
                "private-entry.mjs",
                "wrangler.jsonc",
                "private-settings.sql",
            )
        },
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + NL, encoding="utf-8")
    if source_dir is not None:
        verify_source(source_dir, lock)
    (work / "build-result.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "output": str(output),
                "manifest_sha256": sha256(output / "manifest.json"),
                "source_unchanged": True,
            },
            indent=2,
        )
        + NL,
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", type=Path, help="Optional untouched pinned source; otherwise download it"
    )
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, help="Optional existing task-owned dependency cache"
    )
    args = parser.parse_args()
    try:
        manifest = build(
            args.source_dir.resolve() if args.source_dir else None,
            args.work_dir.resolve(),
            args.output_dir.resolve(),
            cache=args.cache_dir.resolve() if args.cache_dir else None,
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"Build failed: {error}", flush=True)
        return 1
    print(
        json.dumps({"status": "passed", "files": len(cast(list[object], manifest["files"]))}),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
