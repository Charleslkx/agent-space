#!/usr/bin/env bash
set -euo pipefail

mode=${1:-check}
domain=brave.nexuszone.link

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

preflight() {
  [ -r /etc/os-release ] || fail "Ubuntu is required"
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] || fail "Ubuntu is required (found ${ID:-unknown})"
  case "$(uname -m)" in x86_64|aarch64) ;; *) fail "unsupported architecture: $(uname -m)" ;; esac
  need sudo
  need curl
  need openssl
  getent hosts "$domain" >/dev/null || fail "$domain has no DNS result"
  for port in 80 443; do
    ss -ltn "sport = :$port" | grep -q LISTEN && fail "TCP $port is already in use"
  done
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
