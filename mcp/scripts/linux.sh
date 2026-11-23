#!/usr/bin/env bash
# Shared host preparation. Service wrappers supply name and loopback port.
set -euo pipefail
service=${1:?service required}
app_port=${2:?port required}
mode=${3:-check}
domain=${DOMAIN:?set DOMAIN to a public hostname}
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ ${#domain} -le 253 && "$domain" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] || fail 'invalid DOMAIN: use a lowercase hostname without scheme, port or path'
case "$mode" in check|install) ;; *) fail 'usage: scripts/ubuntu.sh [check|install]' ;; esac
[ -r /etc/os-release ] || fail 'Ubuntu or Debian is required'
. /etc/os-release
case "$ID:$VERSION_ID" in
  ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|ubuntu:26.10|debian:12|debian:13) ;;
  *) fail "unsupported OS: $ID $VERSION_ID" ;;
esac
case "$(uname -m)" in x86_64|aarch64) ;; *) fail 'amd64 or arm64 is required' ;; esac
[ "$EUID" -eq 0 ] || fail 'run with sudo env DOMAIN=hostname scripts/ubuntu.sh check (or install)'
command -v systemctl >/dev/null || fail 'systemd is required'
case "$service:$app_port" in brave-search-mcp:8766|firecrawl-mcp:8767|exa-mcp:8769|lark-cli-mcp:8768|lark-doc:8765|colab-cli-mcp:8770) ;; *) fail 'unknown service or mismatched port' ;; esac
for cmd in curl openssl ss getent; do
  command -v "$cmd" >/dev/null || fail "missing $cmd; install prerequisites from DEPLOYMENT.md first"
done
getent ahosts "$domain" >/dev/null || fail "$domain has no DNS result"
# Inspect each occupied port rather than assuming nginx owns both listeners.
for port in 80 443; do
  if ss -H -ltn "sport = :$port" | grep -q .; then
    listeners=$(ss -H -ltnp "sport = :$port")
    while IFS= read -r listener; do
      [[ "$listener" == *'"nginx"'* ]] || fail "TCP $port has a non-nginx or unidentified listener"
    done <<< "$listeners"
  fi
done
if ss -H -ltn "sport = :$app_port" | grep -q .; then
  printf 'TCP %s is occupied; for an existing %s deployment verify ownership before continuing.\n' "$app_port" "$service" >&2
  # Existing deployments can be checked again, but fresh installs cannot claim their port.
  [ "$mode" != install ] || fail 'application port is already occupied; use check for an existing deployment'
fi
df -Pm / | awk 'NR==2 { exit ($4 < 2048) }' || fail 'at least 2 GiB free disk is required'
awk '/MemTotal/ { exit ($2 < 1048576) }' /proc/meminfo || fail 'at least 1 GiB RAM is required'
if [ "$mode" = install ]; then
  apt-get update
  apt-get install -y ca-certificates curl openssl iproute2 python3 python3-venv nginx certbot python3-certbot-nginx
  if [ "$service" != lark-doc ]; then
    if ! command -v docker >/dev/null; then
      # Do not remove a host container runtime automatically.
      for pkg in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx 'install ok installed'; then
          fail "conflicting package $pkg; migrate the existing runtime before installing Docker CE"
        fi
      done
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    docker compose version >/dev/null 2>&1 || fail 'existing Docker lacks Compose plugin; install a compatible plugin from its package source'
    systemctl enable --now docker
  fi
  systemctl enable --now nginx
fi
bash "$(dirname -- "${BASH_SOURCE[0]}")/check-python.sh" --setup
for cmd in nginx certbot; do
  command -v "$cmd" >/dev/null || fail "missing $cmd; install prerequisites from DEPLOYMENT.md first"
done
if [ "$service" != lark-doc ]; then
  docker compose version >/dev/null
  docker compose up --help | grep -q -- '--wait-timeout' || fail 'Compose with --wait-timeout support is required; upgrade the plugin'
  docker info >/dev/null
fi
nginx -t
printf 'Host check passed for %s at %s; verify public DNS and external TCP 80/443 separately.\n' "$service" "$domain"
