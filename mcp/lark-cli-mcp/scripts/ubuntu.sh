#!/usr/bin/env bash
set -euo pipefail

mode=${1:-check}
base_domain=${BASE_DOMAIN:?must set BASE_DOMAIN, for example example.com}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

case "$base_domain" in
  *://*|*/*|*:*|lark.*|.*|*.) fail 'BASE_DOMAIN must be a bare base domain such as example.com; the script adds lark.' ;;
esac
printf '%s' "$base_domain" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$' \
  || fail 'BASE_DOMAIN is not a valid base domain'
domain="lark.$base_domain"

preflight() {
  [ -r /etc/os-release ] || fail 'Ubuntu is required'
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] || fail "Ubuntu is required (found ${ID:-unknown})"
  case "$(uname -m)" in x86_64|aarch64) ;; *) fail "unsupported architecture: $(uname -m)" ;; esac
  need curl
  need openssl
  need getent
  getent hosts "$domain" >/dev/null || fail "$domain has no DNS result"
  ss -ltn "sport = :8768" | grep -q LISTEN && fail 'TCP 8768 is already in use'
  df -Pm / | awk 'NR==2 { exit ($4 < 2048) }' || fail 'at least 2 GiB free disk is required'
  awk '/MemTotal/ { exit ($2 < 1048576) }' /proc/meminfo || fail 'at least 1 GiB RAM is required'
}

runtime_ready() {
  command -v docker >/dev/null 2>&1 || return 1
  docker compose version >/dev/null 2>&1 || return 1
  command -v nginx >/dev/null 2>&1 || return 1
  command -v certbot >/dev/null 2>&1 || return 1
}

check() {
  preflight
  runtime_ready && printf 'environment check passed for %s\n' "$domain" \
    || printf 'preflight passed; run sudo -E %s install to install Docker Compose, Nginx, and Certbot\n' "$0"
}

install() {
  [ "$EUID" -eq 0 ] || fail "install mode must run as: sudo -E BASE_DOMAIN=$base_domain $0 install"
  preflight
  need sudo
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx
  command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sudo sh
  docker compose version >/dev/null 2>&1 || sudo apt-get install -y docker-compose-plugin
  runtime_ready || fail 'Docker Compose, Nginx, or Certbot did not become ready'
  docker info >/dev/null 2>&1 || fail 'Docker daemon is unavailable to root'
  printf 'dependencies installed for %s; use sudo for Docker commands and continue with CONFIGURATION.md\n' "$domain"
}

case "$mode" in
  check) check ;;
  install) install ;;
  *) fail 'usage: scripts/ubuntu.sh [check|install]' ;;
esac
