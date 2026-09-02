#!/usr/bin/env bash
set -euo pipefail

mode=${1:-check}
domain=${DOMAIN:?must set DOMAIN env var (e.g. exa.your-domain.com)}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

nginx_is_serving() {
  command -v nginx >/dev/null 2>&1 || return 1
  systemctl is-active --quiet nginx 2>/dev/null && return 0
  # Fall back to reading the listener's process name, which needs root; if
  # neither signal is available, treat it as not-nginx and let the caller fail
  # loudly rather than assume the port is safe to share.
  ss -ltnp "sport = :80" 2>/dev/null | grep -q 'nginx'
}

preflight() {
  [ -r /etc/os-release ] || fail "Ubuntu is required"
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] || fail "Ubuntu is required (found ${ID:-unknown})"
  case "$(uname -m)" in x86_64|aarch64) ;; *) fail "unsupported architecture: $(uname -m)" ;; esac
  need sudo
  need curl
  need openssl
  getent hosts "$domain" >/dev/null || fail "$domain has no DNS result"
  # This host very likely already serves a sibling MCP, in which case nginx is
  # supposed to be on 80/443 and a new server block just gets added alongside.
  # Only a non-nginx listener is an actual conflict, so the check distinguishes
  # the two instead of refusing to run on every co-hosted machine.
  for port in 80 443; do
    ss -ltn "sport = :$port" | grep -q LISTEN || continue
    if nginx_is_serving; then
      printf 'note: TCP %s is served by nginx; this deployment will add a server block for %s\n' "$port" "$domain"
    else
      fail "TCP $port is in use by something other than nginx"
    fi
  done
  # 8769 is this service's own host port and must be free regardless.
  ss -ltn "sport = :8769" | grep -q LISTEN && fail "TCP 8769 is already in use (another service on this host port?)"
  df -Pm / | awk 'NR==2 { exit ($4 < 2048) }' || fail "at least 2 GiB free disk is required"
  awk '/MemTotal/ { exit ($2 < 1048576) }' /proc/meminfo || fail "at least 1 GiB RAM is required"
}

runtime_ready() {
  command -v docker >/dev/null 2>&1 || return 1
  docker compose version >/dev/null 2>&1 || return 1
  command -v nginx >/dev/null 2>&1 || return 1
  command -v certbot >/dev/null 2>&1 || return 1
}

check() {
  preflight
  if runtime_ready; then
    printf 'environment check passed for %s\n' "$domain"
  else
    printf 'preflight passed; Docker Compose, Nginx, or Certbot is not ready. Run: sudo %s install\n' "$0"
  fi
}

install() {
  preflight
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx
  if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
  fi
  if ! docker compose version >/dev/null 2>&1; then
    sudo apt-get install -y docker-compose-plugin
  fi
  docker compose version >/dev/null
  runtime_ready || fail "Docker Compose, Nginx, or Certbot did not become ready"
  printf 'dependencies installed and verified\n'
}

case "$mode" in
  check) check ;;
  install) install ;;
  *) fail 'usage: scripts/ubuntu.sh [check|install]' ;;
esac
