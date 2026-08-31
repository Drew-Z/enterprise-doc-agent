#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/versions.env"
MODE=""
PROFILE="single-node-4c8g"
ASSET_PATH=""
DOCKER_IO_MIRROR=""
REGISTRY_CONFIG_CHANGED=0
RUNTIME_CONFIG_CHANGED=0

usage() {
  cat <<'EOF'
Usage:
  install-k3s.sh --check [--profile tiny-single-node|single-node-4c4g|single-node-4c8g]
  sudo install-k3s.sh --apply --profile single-node-4c4g [--asset /path/to/k3s] \
    [--docker-io-mirror https://registry-mirror.example.com]

Installs the reviewed K3s binary and single-node systemd service. Packaged Traefik stays enabled.
An operator-supplied asset is accepted only when its SHA-256 matches versions.env.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

test "$K3S_VERSION" = "v1.36.2+k3s1" || die "unreviewed K3s version"
INSTALL_K3S_VERSION="$K3S_VERSION"

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
    --profile)
      shift
      (($#)) || die "--profile requires a value"
      PROFILE="$1"
      shift
      ;;
    --asset)
      shift
      (($#)) || die "--asset requires a path"
      ASSET_PATH="$1"
      shift
      ;;
    --docker-io-mirror)
      shift
      (($#)) || die "--docker-io-mirror requires an HTTPS endpoint"
      DOCKER_IO_MIRROR="${1%/}"
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done
case "$PROFILE" in
  single-node-4c8g)
    NODE_LABEL="single-node-4c8g"
    SYSTEM_RESERVED="cpu=300m,memory=512Mi,ephemeral-storage=1Gi"
    KUBE_RESERVED="cpu=300m,memory=512Mi,ephemeral-storage=1Gi"
    EVICTION_HARD="memory.available<700Mi,nodefs.available<10%,imagefs.available<10%"
    ;;
  single-node-4c4g)
    NODE_LABEL="single-node-4c4g"
    SYSTEM_RESERVED="cpu=250m,memory=384Mi,ephemeral-storage=768Mi"
    KUBE_RESERVED="cpu=250m,memory=384Mi,ephemeral-storage=768Mi"
    EVICTION_HARD="memory.available<384Mi,nodefs.available<10%,imagefs.available<10%"
    ;;
  tiny-single-node)
    NODE_LABEL="tiny-single-node"
    SYSTEM_RESERVED="cpu=150m,memory=256Mi,ephemeral-storage=512Mi"
    KUBE_RESERVED="cpu=150m,memory=256Mi,ephemeral-storage=512Mi"
    EVICTION_HARD="memory.available<256Mi,nodefs.available<10%,imagefs.available<10%"
    ;;
  *)
    die "unsupported host profile: $PROFILE"
    ;;
esac
test -n "$MODE" || { usage >&2; exit 2; }
test "$MODE" = apply || test -z "$ASSET_PATH" || die "--asset is valid only with --apply"
test "$MODE" = apply || test -z "$DOCKER_IO_MIRROR" \
  || die "--docker-io-mirror is valid only with --apply"
if test -n "$DOCKER_IO_MIRROR"; then
  [[ "$DOCKER_IO_MIRROR" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] \
    || die "--docker-io-mirror must be an HTTPS host with an optional port"
fi

wait_system_component() {
  local label="$1"
  local timeout="$2"
  /usr/local/bin/k3s kubectl -n kube-system wait --for=create pod -l "$label" --timeout="$timeout"
  /usr/local/bin/k3s kubectl -n kube-system wait --for=condition=Ready pod -l "$label" --timeout="$timeout"
}

wait_system_components() {
  local timeout="$1"
  wait_system_component k8s-app=kube-dns "$timeout"
  wait_system_component k8s-app=metrics-server "$timeout"
  wait_system_component app.kubernetes.io/name=traefik "$timeout"
}

wait_node_object() {
  local timeout_seconds="${1%s}"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if /usr/local/bin/k3s kubectl get nodes --no-headers 2>/dev/null | grep -q .; then
      return 0
    fi
    sleep 2
  done
  die "K3s Node object did not appear within ${timeout_seconds}s"
}

wait_traefik_private_service() {
  local timeout_seconds="${1%s}"
  local deadline=$((SECONDS + timeout_seconds))
  local service_type=""
  while ((SECONDS < deadline)); do
    service_type="$(/usr/local/bin/k3s kubectl -n kube-system get service traefik \
      -o jsonpath='{.spec.type}' 2>/dev/null || true)"
    test "$service_type" = ClusterIP && return 0
    sleep 2
  done
  die "Traefik Service did not converge to ClusterIP within ${timeout_seconds}s"
}

wait_traefik_loopback() {
  local timeout_seconds="${1%s}"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if curl --insecure --silent --output /dev/null \
      --connect-timeout 2 --max-time 3 https://127.0.0.1:8443/; then
      return 0
    fi
    sleep 2
  done
  die "Traefik loopback TLS endpoint did not become reachable within ${timeout_seconds}s"
}

configure_registry_mirror() {
  test -n "$DOCKER_IO_MIRROR" || return 0
  install -d -o root -g root -m 0700 /etc/rancher/k3s
  local candidate
  candidate="$(mktemp)"
  printf 'mirrors:\n  "docker.io":\n    endpoint:\n      - "%s"\n' "$DOCKER_IO_MIRROR" >"$candidate"
  if test ! -f /etc/rancher/k3s/registries.yaml \
    || ! cmp --silent "$candidate" /etc/rancher/k3s/registries.yaml; then
    install -o root -g root -m 0600 "$candidate" /etc/rancher/k3s/registries.yaml
    REGISTRY_CONFIG_CHANGED=1
  fi
  rm -f "$candidate"
}

configure_k3s_runtime() {
  install -d -o root -g root -m 0700 /etc/rancher/k3s
  install -d -o root -g root -m 0755 /var/lib/rancher/k3s/server/manifests

  local candidate
  candidate="$(mktemp)"
  cat >"$candidate" <<EOF
write-kubeconfig-mode: "0600"
secrets-encryption: true
flannel-backend: vxlan
disable:
  - servicelb
node-label:
  - "enterprise-doc-agent/profile=$NODE_LABEL"
kubelet-arg:
  - "system-reserved=$SYSTEM_RESERVED"
  - "kube-reserved=$KUBE_RESERVED"
  - "eviction-hard=$EVICTION_HARD"
EOF
  if test ! -f /etc/rancher/k3s/config.yaml \
    || ! cmp --silent "$candidate" /etc/rancher/k3s/config.yaml; then
    install -o root -g root -m 0600 "$candidate" /etc/rancher/k3s/config.yaml
    RUNTIME_CONFIG_CHANGED=1
  fi

  cat >"$candidate" <<'EOF'
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    additionalArguments:
      - "--entryPoints.web.address=:8000/tcp"
      - "--entryPoints.websecure.address=:8443/tcp"
    updateStrategy:
      type: Recreate
    service:
      spec:
        type: ClusterIP
    ports:
      web:
        hostPort: 8080
        hostIP: 127.0.0.1
      websecure:
        hostPort: 8443
        hostIP: 127.0.0.1
EOF
  if test ! -f /var/lib/rancher/k3s/server/manifests/enterprise-doc-traefik-config.yaml \
    || ! cmp --silent "$candidate" \
      /var/lib/rancher/k3s/server/manifests/enterprise-doc-traefik-config.yaml; then
    install -o root -g root -m 0644 "$candidate" \
      /var/lib/rancher/k3s/server/manifests/enterprise-doc-traefik-config.yaml
    RUNTIME_CONFIG_CHANGED=1
  fi
  rm -f "$candidate"
}

check_cluster() {
  local timeout="${1:-30s}"
  test -x /usr/local/bin/k3s || die "k3s is not installed"
  test "$(/usr/local/bin/k3s --version | awk 'NR == 1 {print $3}')" = "$K3S_VERSION"
  systemctl is-active --quiet k3s
  /usr/local/bin/k3s kubectl wait --for=condition=Ready node --all --timeout="$timeout"
  wait_system_components "$timeout"
  wait_traefik_private_service "$timeout"
  wait_traefik_loopback "$timeout"
}

if test "$MODE" = check; then
  check_cluster
  printf 'profile=%s\nk3s_version=%s\ntraefik=packaged\n' "$PROFILE" "$K3S_VERSION"
  exit 0
fi

test "$(id -u)" -eq 0 || die "--apply must run as root"
configure_registry_mirror
configure_k3s_runtime
if systemctl is-active --quiet k3s 2>/dev/null; then
  if test "$REGISTRY_CONFIG_CHANGED" -eq 1 || test "$RUNTIME_CONFIG_CHANGED" -eq 1; then
    systemctl restart k3s
    check_cluster 300s
  else
    check_cluster 30s
  fi
  exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
if test -n "$ASSET_PATH"; then
  test -f "$ASSET_PATH" || die "K3s asset does not exist: $ASSET_PATH"
  asset_path="$ASSET_PATH"
else
  asset_url="https://github.com/k3s-io/k3s/releases/download/${K3S_VERSION/+/%2B}/k3s"
  asset_path="$tmp_dir/k3s"
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 900 --retry 4 --retry-all-errors \
    --output "$asset_path" "$asset_url"
fi
printf '%s  %s\n' "$K3S_AMD64_SHA256" "$asset_path" | sha256sum --check --status \
  || die "K3s asset SHA-256 mismatch"
install -o root -g root -m 0755 "$asset_path" /usr/local/bin/k3s
ln -sfn /usr/local/bin/k3s /usr/local/bin/kubectl
ln -sfn /usr/local/bin/k3s /usr/local/bin/crictl
ln -sfn /usr/local/bin/k3s /usr/local/bin/ctr

cat >/etc/systemd/system/k3s.service <<'EOF'
[Unit]
Description=Lightweight Kubernetes
Documentation=https://k3s.io
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
Environment=K3S_CONFIG_FILE=/etc/rancher/k3s/config.yaml
KillMode=process
Delegate=yes
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
TimeoutStartSec=0
Restart=always
RestartSec=5s
ExecStart=/usr/local/bin/k3s server

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now k3s

wait_node_object 300s
kubectl wait --for=condition=Ready node --all --timeout=300s
wait_system_components 300s
wait_traefik_private_service 300s
wait_traefik_loopback 300s
printf 'k3s %s installed with packaged Traefik\n' "$K3S_VERSION"
