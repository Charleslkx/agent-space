#!/usr/bin/env bash
# Manage credentials in the running server's env file.
# Never echoes a full secret, never writes a secret anywhere but the target env file.
# The one exception is `token add`, which must print the new token once because
# there is no other way to hand it to a client that cannot do OAuth.
#
# Usage:
#   scripts/apikey.sh set [KEY]   # prompts (hidden input) if KEY is omitted
#   scripts/apikey.sh show        # masked
#   scripts/apikey.sh verify      # checks the currently configured key, changes nothing
#   scripts/apikey.sh delete
#   scripts/apikey.sh oauth set [CLIENT_ID]  # prompts for Client ID and Client Secret
#   scripts/apikey.sh oauth show             # shows Client ID and a masked Client Secret
#   scripts/apikey.sh token add LOGIN        # mint a static bearer token (for Trae)
#   scripts/apikey.sh token list             # masked
#   scripts/apikey.sh token delete LOGIN
set -euo pipefail

ENV_FILE="${EXA_MCP_ENV_FILE:-/etc/exa-mcp.env}"
VAR=EXA_API_KEY
OAUTH_ID_VAR=EXA_MCP_GITHUB_CLIENT_ID
OAUTH_SECRET_VAR=EXA_MCP_GITHUB_CLIENT_SECRET
TOKENS_VAR=EXA_MCP_STATIC_TOKENS
USERS_VAR=EXA_MCP_GITHUB_USERS
EXA_API_URL="${EXA_API_URL:-https://api.exa.ai}"
CMD="${1:-}"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need curl
need grep
need openssl

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

# Deliberately empty body: the request is rejected on its parameters, so no
# search runs and no credits are spent. Exa documents 401/INVALID_API_KEY as a
# distinct failure from 400/INVALID_REQUEST_BODY, which is what makes a bad key
# distinguishable from a bad body.
#
# The MCP endpoint is NOT usable for this: its gateway answers initialize and
# tools/list for any syntactically present key, so it cannot tell a good key
# from a bad one. Only the REST API actually authenticates.
probe_search() {
  local key="$1" args=(-sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json')
  [ -n "$key" ] && args+=(-H "x-api-key: ${key}")
  # No -f: an HTTP error must still print its status code so the caller can
  # tell "bad key" from "unreachable network".
  curl "${args[@]}" -d '{}' "${EXA_API_URL}/search"
}

# Prints one of: valid | invalid | no-credits | inconclusive <detail>
#
# Rather than assuming Exa validates auth before the request body, this probes
# twice -- once with the configured key, once with a key that cannot be real --
# and compares. That self-calibrates: if both come back identical, body
# validation runs first, the probe cannot see auth at all, and it says so
# instead of guessing.
classify_key() {
  local key="$1" configured bogus
  configured=$(probe_search "$key") || return 1
  bogus=$(probe_search "exa-mcp-deliberately-invalid-key-$$") || return 1
  case "$configured" in
    401) printf 'invalid\n' ;;
    402) printf 'no-credits\n' ;;
    200) printf 'valid\n' ;;
    400|422)
      if [ "$bogus" = "401" ]; then
        printf 'valid\n'
      else
        printf 'inconclusive the API answered %s for both a real and a bogus key, so this probe cannot see authentication\n' "$configured"
      fi
      ;;
    *) printf 'inconclusive unexpected HTTP %s (bogus key got %s)\n' "$configured" "$bogus" ;;
  esac
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

remove_value() {
  local var="$1" tmp
  tmp=$(mktemp "$(dirname "$ENV_FILE")/.apikey.XXXXXX")
  grep -v "^${var}=" "$ENV_FILE" > "$tmp" || true
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

# Static tokens live in one comma-separated `login:token` variable, so editing
# one entry means rewriting the whole list.
tokens_without() {
  local drop="$1" current entry login rebuilt=""
  current=$(current_value "$TOKENS_VAR") || true
  IFS=',' read -ra entries <<< "${current:-}"
  for entry in "${entries[@]:-}"; do
    [ -n "$entry" ] || continue
    login="${entry%%:*}"
    [ "$login" = "$drop" ] && continue
    rebuilt="${rebuilt:+$rebuilt,}$entry"
  done
  printf '%s' "$rebuilt"
}

assert_allowlisted() {
  local login="$1" users
  users=$(current_value "$USERS_VAR") || true
  # The server refuses to boot on a token whose login is not in the allowlist,
  # so catching it here saves a failed restart.
  case ",${users}," in
    *",${login},"*) return 0 ;;
  esac
  fail "$login is not listed in $USERS_VAR; add it there first or the server will refuse to boot"
}

recreate_container() {
  # env_file is only read at container creation; `restart` would not pick up the change.
  ( cd "$(dirname "${BASH_SOURCE[0]}")/.." && docker compose up -d --force-recreate mcp )
}

ready_to_start() {
  local required var value
  required=(
    EXA_MCP_BASE_URL
    EXA_MCP_GITHUB_CLIENT_ID
    EXA_MCP_GITHUB_CLIENT_SECRET
    EXA_MCP_GITHUB_USERS
    EXA_MCP_JWT_SIGNING_KEY
    EXA_MCP_STORAGE_KEY
    EXA_MCP_REDIS_PASSWORD
    EXA_API_KEY
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
      read -rsp 'New Exa API key: ' key
      printf '\n'
    fi
    [ -n "$key" ] || fail "empty key"
    verdict=$(classify_key "$key") || fail "could not reach ${EXA_API_URL} to verify the key"
    case "$verdict" in
      invalid) fail 'Exa rejected that key (401 INVALID_API_KEY); not saved' ;;
      # Both of these are still worth saving: the key itself is accepted, and
      # refusing to store it would leave the server with no key at all.
      no-credits) printf 'warning: key is valid but the account is out of credits (402).\n' >&2 ;;
      inconclusive*) printf 'warning: could not confirm the key -- %s\n' "${verdict#inconclusive }" >&2 ;;
    esac
    write_value "$VAR" "$key"
    recreate_when_ready
    printf '%s updated.\n' "$VAR"
    ;;
  show)
    key=$(current_value "$VAR") || true
    [ -n "$key" ] || fail "$VAR is not set in $ENV_FILE"
    printf '%s = %s (modified %s)\n' "$VAR" "$(mask "$key")" "$(date -r "$ENV_FILE" 2>/dev/null || stat -f '%Sm' "$ENV_FILE" 2>/dev/null || echo unknown)"
    ;;
  verify)
    key=$(current_value "$VAR") || true
    [ -n "$key" ] || fail "$VAR is not set in $ENV_FILE"
    verdict=$(classify_key "$key") || fail "could not reach ${EXA_API_URL}"
    case "$verdict" in
      valid) printf 'ok: current key is accepted by Exa\n' ;;
      invalid) fail 'current key is rejected by Exa (401 INVALID_API_KEY)' ;;
      no-credits) fail 'current key is valid but the account is out of credits (402); top up at dashboard.exa.ai' ;;
      *) fail "could not verify the current key -- ${verdict#inconclusive }" ;;
    esac
    ;;
  delete)
    remove_value "$VAR"
    recreate_container
    printf '%s removed; container recreated. The server will now refuse to boot with a clear\n' "$VAR"
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
  token)
    case "${2:-}" in
      add)
        login="${3:-}"
        [ -n "$login" ] || fail 'usage: scripts/apikey.sh token add LOGIN'
        case "$login" in
          *:*|*,*) fail 'LOGIN must not contain ":" or ","' ;;
        esac
        login=$(printf '%s' "$login" | tr '[:upper:]' '[:lower:]')
        assert_allowlisted "$login"
        token=$(openssl rand -hex 32)
        rest=$(tokens_without "$login")
        write_value "$TOKENS_VAR" "${rest:+$rest,}${login}:${token}"
        recreate_when_ready
        printf 'Static token for %s (shown once, it is not recoverable from the env file in cleartext form later):\n\n' "$login"
        printf '  Authorization: Bearer %s\n\n' "$token"
        printf 'Any previous token for %s has been replaced.\n' "$login"
        ;;
      list)
        current=$(current_value "$TOKENS_VAR") || true
        if [ -z "${current:-}" ]; then
          printf '%s is not set; OAuth is the only way in.\n' "$TOKENS_VAR"
          exit 0
        fi
        IFS=',' read -ra entries <<< "$current"
        for entry in "${entries[@]}"; do
          [ -n "$entry" ] || continue
          printf '%s = %s\n' "${entry%%:*}" "$(mask "${entry#*:}")"
        done
        ;;
      delete)
        login="${3:-}"
        [ -n "$login" ] || fail 'usage: scripts/apikey.sh token delete LOGIN'
        login=$(printf '%s' "$login" | tr '[:upper:]' '[:lower:]')
        rest=$(tokens_without "$login")
        if [ -z "$rest" ]; then
          remove_value "$TOKENS_VAR"
        else
          write_value "$TOKENS_VAR" "$rest"
        fi
        recreate_container
        printf 'Static token for %s removed; container recreated.\n' "$login"
        ;;
      *) fail 'usage: scripts/apikey.sh token [add LOGIN|list|delete LOGIN]' ;;
    esac
    ;;
  *)
    fail 'usage: scripts/apikey.sh [set [KEY]|show|verify|delete|oauth [set [CLIENT_ID]|show]|token [add LOGIN|list|delete LOGIN]]'
    ;;
esac
