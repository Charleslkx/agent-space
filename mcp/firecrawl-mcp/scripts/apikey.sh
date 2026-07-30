#!/usr/bin/env bash
# Manage credentials in the running server's env file.
# Never echoes a full secret, never writes a secret anywhere but the target env file.
#
# Usage:
#   scripts/apikey.sh set [KEY]   # prompts (hidden input) if KEY is omitted
#   scripts/apikey.sh show        # masked
#   scripts/apikey.sh verify      # checks the currently configured key, changes nothing
#   scripts/apikey.sh delete
#   scripts/apikey.sh oauth set [CLIENT_ID]  # prompts for Client ID and Client Secret
#   scripts/apikey.sh oauth show              # shows Client ID and a masked Client Secret
set -euo pipefail

ENV_FILE="${FIRECRAWL_MCP_ENV_FILE:-/etc/firecrawl-mcp.env}"
VAR=FIRECRAWL_API_KEY
OAUTH_ID_VAR=FIRECRAWL_MCP_GITHUB_CLIENT_ID
OAUTH_SECRET_VAR=FIRECRAWL_MCP_GITHUB_CLIENT_SECRET
CMD="${1:-}"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need curl
need grep

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"

current_value() {
  local var="$1"
  grep -m1 "^${var}=" "$ENV_FILE" | cut -d= -f2-
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

write_value() {
  local var="$1" value="$2" tmp
  case "$value" in
    *$'\n'*|*$'\r'*) fail "value for $var must not contain a newline" ;;
  esac
  tmp=$(mktemp "$(dirname "$ENV_FILE")/.apikey.XXXXXX")
  if grep -q "^${var}=" "$ENV_FILE"; then
    awk -v var="$var" -v val="$value" -F= 'BEGIN{OFS="="} $1==var{$0=var"="val} {print}' "$ENV_FILE" > "$tmp"
  else
    cp "$ENV_FILE" "$tmp"
    printf '%s=%s\n' "$var" "$value" >> "$tmp"
  fi
  chmod 600 "$tmp"
  chown root:root "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
}

write_oauth() {
  local client_id="$1" client_secret="$2" tmp
  case "$client_id$client_secret" in
    *$'\n'*|*$'\r'*) fail 'OAuth values must not contain a newline' ;;
  esac
  tmp=$(mktemp "$(dirname "$ENV_FILE")/.apikey.XXXXXX")
  awk -v id_var="$OAUTH_ID_VAR" -v id="$client_id" \
      -v secret_var="$OAUTH_SECRET_VAR" -v secret="$client_secret" -F= '
    BEGIN { found_id=0; found_secret=0 }
    $1 == id_var { print id_var "=" id; found_id=1; next }
    $1 == secret_var { print secret_var "=" secret; found_secret=1; next }
    { print }
    END {
      if (!found_id) print id_var "=" id
      if (!found_secret) print secret_var "=" secret
    }
  ' "$ENV_FILE" > "$tmp"
  chmod 600 "$tmp"
  chown root:root "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
}

recreate_container() {
  # env_file is only read at container creation; `restart` would not pick up the change.
  ( cd "$(dirname "${BASH_SOURCE[0]}")/.." && docker compose up -d --force-recreate mcp )
}

ready_to_start() {
  local required var value
  required=(
    FIRECRAWL_MCP_BASE_URL
    FIRECRAWL_MCP_GITHUB_CLIENT_ID
    FIRECRAWL_MCP_GITHUB_CLIENT_SECRET
    FIRECRAWL_MCP_GITHUB_USERS
    FIRECRAWL_MCP_JWT_SIGNING_KEY
    FIRECRAWL_MCP_STORAGE_KEY
    FIRECRAWL_MCP_REDIS_PASSWORD
    FIRECRAWL_API_KEY
  )
  for var in "${required[@]}"; do
    value=$(current_value "$var") || return 1
    [ -n "$value" ] && [ "$value" != 'REPLACE_ME' ] || return 1
  done
}

recreate_when_ready() {
  if ready_to_start; then
    recreate_container
    return 0
  fi
  printf 'Credential saved. Service was not started because another required credential is still unset.\n'
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
    write_value "$VAR" "$key"
    recreate_when_ready
    printf 'FIRECRAWL_API_KEY updated and verified (HTTP 200).\n'
    ;;
  show)
    key=$(current_value "$VAR") || true
    [ -n "$key" ] || fail "$VAR is not set in $ENV_FILE"
    printf '%s = %s (modified %s)\n' "$VAR" "$(mask "$key")" "$(date -r "$ENV_FILE" 2>/dev/null || stat -f '%Sm' "$ENV_FILE" 2>/dev/null || echo unknown)"
    ;;
  verify)
    key=$(current_value "$VAR") || true
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
  oauth)
    case "${2:-}" in
      set)
        client_id="${3:-}"
        if [ -z "$client_id" ]; then
          read -rp 'GitHub OAuth Client ID: ' client_id
        fi
        [ -n "$client_id" ] || fail 'empty GitHub OAuth Client ID'
        # Deliberately do not accept a secret argument: command arguments can be
        # exposed through shell history and process listings.
        read -rsp 'GitHub OAuth Client Secret: ' client_secret
        printf '\n'
        [ -n "$client_secret" ] || fail 'empty GitHub OAuth Client Secret'
        write_oauth "$client_id" "$client_secret"
        recreate_when_ready
        printf 'GitHub OAuth Client ID and Client Secret updated.\n'
        ;;
      show)
        client_id=$(current_value "$OAUTH_ID_VAR") || true
        client_secret=$(current_value "$OAUTH_SECRET_VAR") || true
        [ -n "$client_id" ] || fail "$OAUTH_ID_VAR is not set in $ENV_FILE"
        [ -n "$client_secret" ] || fail "$OAUTH_SECRET_VAR is not set in $ENV_FILE"
        printf '%s = %s\n%s = %s\n' "$OAUTH_ID_VAR" "$client_id" "$OAUTH_SECRET_VAR" "$(mask "$client_secret")"
        ;;
      *) fail 'usage: scripts/apikey.sh oauth [set [CLIENT_ID]|show]' ;;
    esac
    ;;
  *)
    fail 'usage: scripts/apikey.sh [set [KEY]|show|verify|delete|oauth [set [CLIENT_ID]|show]]'
    ;;
esac
