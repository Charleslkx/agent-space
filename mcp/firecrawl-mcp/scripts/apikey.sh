#!/usr/bin/env bash
# Manage the FIRECRAWL_API_KEY entry in the running server's env file.
# Never echoes the full key, never writes it anywhere but the target env file.
#
# Usage:
#   scripts/apikey.sh set [KEY]   # prompts (hidden input) if KEY is omitted
#   scripts/apikey.sh show        # masked
#   scripts/apikey.sh verify      # checks the currently configured key, changes nothing
#   scripts/apikey.sh delete
set -euo pipefail

ENV_FILE="${FIRECRAWL_MCP_ENV_FILE:-/etc/firecrawl-mcp.env}"
VAR=FIRECRAWL_API_KEY
CMD="${1:-}"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need curl
need grep

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"

current_key() {
  grep -m1 "^${VAR}=" "$ENV_FILE" | cut -d= -f2-
}

mask() {
  local key="$1" len
  len=${#key}
  if [ "$len" -le 8 ]; then
    printf '%s\n' "****"
  else
    printf '%s****%s\n' "${key:0:4}" "${key: -4}"
  fi
}

check_key() {
  # No -f: an HTTP error (e.g. 401 for a bad key) must still print its status
  # code so the caller can distinguish "bad key" from "unreachable network".
  local key="$1"
  curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${key}" \
    "https://api.firecrawl.dev/v2/team/credit-usage"
}

write_key() {
  local key="$1" tmp
  tmp=$(mktemp "$(dirname "$ENV_FILE")/.apikey.XXXXXX")
  if grep -q "^${VAR}=" "$ENV_FILE"; then
    awk -v var="$VAR" -v val="$key" -F= 'BEGIN{OFS="="} $1==var{$0=var"="val} {print}' "$ENV_FILE" > "$tmp"
  else
    cp "$ENV_FILE" "$tmp"
    printf '%s=%s\n' "$VAR" "$key" >> "$tmp"
  fi
  chmod 600 "$tmp"
  chown root:root "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
}

recreate_container() {
  # env_file is only read at container creation; `restart` would not pick up the change.
  ( cd "$(dirname "${BASH_SOURCE[0]}")/.." && docker compose up -d --force-recreate mcp )
}

case "$CMD" in
  set)
    key="${2:-}"
    if [ -z "$key" ]; then
      read -rsp 'New Firecrawl API key: ' key
      printf '\n'
    fi
    [ -n "$key" ] || fail "empty key"
    status=$(check_key "$key") || fail "could not reach api.firecrawl.dev to verify the key"
    [ "$status" = "200" ] || fail "key verification failed (HTTP $status); not saved"
    write_key "$key"
    recreate_container
    printf 'FIRECRAWL_API_KEY updated and verified (HTTP 200); container recreated.\n'
    ;;
  show)
    key=$(current_key) || true
    [ -n "$key" ] || fail "$VAR is not set in $ENV_FILE"
    printf '%s = %s (modified %s)\n' "$VAR" "$(mask "$key")" "$(date -r "$ENV_FILE" 2>/dev/null || stat -f '%Sm' "$ENV_FILE" 2>/dev/null || echo unknown)"
    ;;
  verify)
    key=$(current_key) || true
    [ -n "$key" ] || fail "$VAR is not set in $ENV_FILE"
    status=$(check_key "$key") || fail "could not reach api.firecrawl.dev"
    if [ "$status" = "200" ]; then
      printf 'ok: current key is valid (HTTP 200)\n'
    else
      fail "current key failed verification (HTTP $status)"
    fi
    ;;
  delete)
    grep -v "^${VAR}=" "$ENV_FILE" > "${ENV_FILE}.tmp"
    chmod 600 "${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
    recreate_container
    printf 'FIRECRAWL_API_KEY removed; container recreated. Every tool call will now fail with a clear\n'
    printf '"missing required environment variable" error -- it will NOT silently fall back to the keyless tier.\n'
    ;;
  *)
    fail 'usage: scripts/apikey.sh [set [KEY]|show|verify|delete]'
    ;;
esac
