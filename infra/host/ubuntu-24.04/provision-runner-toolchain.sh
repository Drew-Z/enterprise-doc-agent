#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/versions.env"
MODE=""
KUSTOMIZE_ASSET_PATH=""
RUNNER_ASSET_PATH=""
POSTGRES_KEY_ASSET_PATH=""

usage() {
  cat <<'EOF'
Usage:
  provision-runner-toolchain.sh --check
  sudo provision-runner-toolchain.sh --apply \
    [--kustomize-asset /path/to/kustomize.tar.gz] \
    [--runner-asset /path/to/actions-runner.tar.gz] \
    [--postgres-key-asset /path/to/apt.postgresql.org.asc]

Installs the pinned root-owned deployment toolchain and unpacked repository runner.
It does not register the runner and accepts no GitHub token.
Operator-supplied assets are accepted only when their SHA-256 matches versions.env.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --check | --apply)
      test -z "$MODE" || die "choose exactly one mode"
      MODE="${1#--}"
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    --kustomize-asset | --runner-asset | --postgres-key-asset)
      option="$1"
      shift
      (($#)) || die "$option requires a path"
      if test "$option" = --kustomize-asset; then
        KUSTOMIZE_ASSET_PATH="$1"
      elif test "$option" = --runner-asset; then
        RUNNER_ASSET_PATH="$1"
      else
        POSTGRES_KEY_ASSET_PATH="$1"
      fi
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done
test -n "$MODE" || { usage >&2; exit 2; }
if test "$MODE" != apply && {
  test -n "$KUSTOMIZE_ASSET_PATH" \
    || test -n "$RUNNER_ASSET_PATH" \
    || test -n "$POSTGRES_KEY_ASSET_PATH"
}; then
  die "asset paths are valid only with --apply"
fi

check_toolchain() {
  test "$(kustomize version)" = "$KUSTOMIZE_VERSION"
  test "$(/opt/enterprise-doc-toolchain/python/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = "$PYTHON_MINOR"
  /opt/enterprise-doc-toolchain/python/bin/python - <<PY
import cryptography
import yaml
assert cryptography.__version__ == "$CRYPTOGRAPHY_VERSION"
assert yaml.__version__ == "$PYYAML_VERSION"
PY
  test -x /opt/actions-runner/run.sh
  test "$(stat -c '%U' /opt/actions-runner)" = "$RUNNER_USER"
  test "$(/opt/actions-runner/bin/Runner.Listener --version)" = "$RUNNER_VERSION"
  test "$(dpkg-query --show --showformat='${Version}' \
    "postgresql-client-$POSTGRES_CLIENT_MAJOR")" = "$POSTGRES_CLIENT_PACKAGE_VERSION"
  test "$(pg_dump --version | sed -E 's/^pg_dump \(PostgreSQL\) ([0-9]+).*/\1/')" \
    = "$POSTGRES_CLIENT_MAJOR"
  test "$(pg_restore --version | sed -E 's/^pg_restore \(PostgreSQL\) ([0-9]+).*/\1/')" \
    = "$POSTGRES_CLIENT_MAJOR"
}

if test "$MODE" = check; then
  check_toolchain
  printf 'runner_version=%s\nrunner_label=%s\nkustomize_version=%s\npostgres_client_package=%s\n' \
    "$(/opt/actions-runner/bin/Runner.Listener --version)" "$RUNNER_LABEL" \
    "$KUSTOMIZE_VERSION" "$POSTGRES_CLIENT_PACKAGE_VERSION"
  exit 0
fi

test "$(id -u)" -eq 0 || die "--apply must run as root"
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$RUNNER_USER"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

postgres_key_asset="apt.postgresql.org.asc"
if test -n "$POSTGRES_KEY_ASSET_PATH"; then
  test -f "$POSTGRES_KEY_ASSET_PATH" \
    || die "PostgreSQL repository key does not exist: $POSTGRES_KEY_ASSET_PATH"
  postgres_key_asset_path="$POSTGRES_KEY_ASSET_PATH"
else
  postgres_key_asset_path="$tmp_dir/$postgres_key_asset"
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 300 --retry 4 --retry-all-errors \
    --output "$postgres_key_asset_path" \
    "https://www.postgresql.org/media/keys/ACCC4CF8.asc"
fi
printf '%s  %s\n' "$POSTGRES_PGDG_KEY_SHA256" "$postgres_key_asset_path" \
  | sha256sum --check --status || die "PostgreSQL repository key SHA-256 mismatch"
install -d -o root -g root -m 0755 /usr/share/postgresql-common/pgdg
install -o root -g root -m 0644 "$postgres_key_asset_path" \
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
cat >/etc/apt/sources.list.d/pgdg.sources <<'EOF'
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: noble-pgdg
Architectures: amd64
Components: main
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update
candidate="$(apt-cache policy "postgresql-client-$POSTGRES_CLIENT_MAJOR" \
  | awk '/Candidate:/ { print $2; exit }')"
test "$candidate" = "$POSTGRES_CLIENT_PACKAGE_VERSION" \
  || die "reviewed PostgreSQL client package is unavailable: $POSTGRES_CLIENT_PACKAGE_VERSION"
apt-get install -y --no-install-recommends --allow-downgrades \
  "postgresql-client-$POSTGRES_CLIENT_MAJOR=$POSTGRES_CLIENT_PACKAGE_VERSION"

kustomize_asset="kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"
if test -n "$KUSTOMIZE_ASSET_PATH"; then
  test -f "$KUSTOMIZE_ASSET_PATH" || die "Kustomize asset does not exist: $KUSTOMIZE_ASSET_PATH"
  kustomize_asset_path="$KUSTOMIZE_ASSET_PATH"
else
  kustomize_asset_path="$tmp_dir/$kustomize_asset"
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 300 --retry 4 --retry-all-errors \
    --output "$kustomize_asset_path" \
    "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/${kustomize_asset}"
fi
printf '%s  %s\n' "$KUSTOMIZE_LINUX_AMD64_SHA256" "$kustomize_asset_path" \
  | sha256sum --check --status || die "Kustomize asset SHA-256 mismatch"
tar -xzf "$kustomize_asset_path" -C "$tmp_dir" kustomize
install -o root -g root -m 0755 "$tmp_dir/kustomize" /usr/local/bin/kustomize

install -d -o root -g root -m 0755 /opt/enterprise-doc-toolchain
python3.12 -m venv /opt/enterprise-doc-toolchain/python
/opt/enterprise-doc-toolchain/python/bin/python -m pip install --disable-pip-version-check \
  --no-cache-dir "PyYAML==$PYYAML_VERSION" "cryptography==$CRYPTOGRAPHY_VERSION"
chown -R root:root /opt/enterprise-doc-toolchain
chmod -R go-w /opt/enterprise-doc-toolchain

runner_asset="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
if test -n "$RUNNER_ASSET_PATH"; then
  test -f "$RUNNER_ASSET_PATH" || die "Actions runner asset does not exist: $RUNNER_ASSET_PATH"
  runner_asset_path="$RUNNER_ASSET_PATH"
else
  runner_asset_path="$tmp_dir/$runner_asset"
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 900 --retry 4 --retry-all-errors \
    --output "$runner_asset_path" \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${runner_asset}"
fi
printf '%s  %s\n' "$RUNNER_SHA256" "$runner_asset_path" \
  | sha256sum --check --status || die "Actions runner asset SHA-256 mismatch"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 /opt/actions-runner
observed_runner_version=""
if test -x /opt/actions-runner/bin/Runner.Listener; then
  observed_runner_version="$(/opt/actions-runner/bin/Runner.Listener --version)"
fi
if test "$observed_runner_version" != "$RUNNER_VERSION"; then
  test ! -e /opt/actions-runner/.runner \
    || die "refusing to replace a registered Actions runner; unregister it first"
  runner_staging="/opt/actions-runner.new.$$"
  runner_previous="/opt/actions-runner.previous.$$"
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$runner_staging"
  tar -xzf "$runner_asset_path" -C "$runner_staging"
  chown -R "$RUNNER_USER:$RUNNER_USER" "$runner_staging"
  mv /opt/actions-runner "$runner_previous"
  mv "$runner_staging" /opt/actions-runner
  rm -rf "$runner_previous"
fi

check_toolchain
printf 'toolchain installed; register the repository runner in a separate token-bearing step\n'
