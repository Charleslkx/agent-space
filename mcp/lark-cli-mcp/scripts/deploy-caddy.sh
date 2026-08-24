#!/usr/bin/env bash
# Deploy Lark CLI MCP on this host behind the existing Caddy instance.
# Run from any directory: sudo ./scripts/deploy-caddy.sh
set -euo pipefail

base_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
domain='{your-domain}'
env_file='/etc/lark-cli-mcp.env'
redis_env_file='/etc/lark-cli-mcp.redis.env'
caddy_file='/etc/caddy/Caddyfile'
caddy_backup="${caddy_file}.before-lark-cli-mcp-$(date +%Y%m%d-%H%M%S)"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail 'run this script with sudo'
command -v docker >/dev/null || fail 'Docker is not installed; run scripts/ubuntu.sh install first'
docker compose version >/dev/null || fail 'Docker Compose is not available'
command -v caddy >/dev/null || fail 'Caddy is not installed'
systemctl is-active --quiet caddy || fail 'Caddy is not running'
getent hosts "$domain" >/dev/null || fail "$domain has no DNS result"

if [[ -e $env_file || -e $redis_env_file ]]; then
  fail "existing environment files found; inspect $env_file before replacing credentials"
fi
if grep -Fqx "$domain {" "$caddy_file"; then
  fail "$domain is already configured in $caddy_file"
fi

read -r -s -p 'GitHub OAuth client secret (input is hidden): ' github_secret
printf '\n'
[[ -n $github_secret ]] || fail 'GitHub OAuth client secret cannot be empty'
[[ $github_secret != *$'\n'* && $github_secret != *$'\r'* ]] || fail 'GitHub OAuth client secret must be one line'

jwt_key=$(openssl rand -hex 32)
storage_key=$(openssl rand -base64 32)
redis_password=$(openssl rand -hex 32)
umask 077
{
  printf '%s\n' 'LARK_CLI_MCP_BASE_URL=https://{your-domain}'
  printf '%s\n' 'LARK_CLI_MCP_GITHUB_CLIENT_ID=REPLACE_ME_GITHUB_CLIENT_ID'
  printf 'LARK_CLI_MCP_GITHUB_CLIENT_SECRET=%s\n' "$github_secret"
  printf '%s\n' 'LARK_CLI_MCP_GITHUB_USERS=REPLACE_ME_GITHUB_USER'
  printf 'LARK_CLI_MCP_JWT_SIGNING_KEY=%s\n' "$jwt_key"
  printf 'LARK_CLI_MCP_STORAGE_KEY=%s\n' "$storage_key"
  printf 'LARK_CLI_MCP_REDIS_PASSWORD=%s\n' "$redis_password"
  printf '%s\n' 'LARK_CLI_MCP_UPDATE_CHECK=1'
} >"$env_file"
printf 'LARK_CLI_MCP_REDIS_PASSWORD=%s\n' "$redis_password" >"$redis_env_file"
chmod 600 "$env_file" "$redis_env_file"
unset github_secret jwt_key storage_key redis_password

cd "$base_dir"
docker compose build
docker compose up -d
docker compose ps

cp "$caddy_file" "$caddy_backup"
cat >>"$caddy_file" <<'CADDY'

# Lark CLI MCP: TLS terminates here; upstream remains loopback-only.
{your-domain} {
	reverse_proxy 127.0.0.1:8768 {
		flush_interval -1
		transport http {
			read_timeout 120s
			write_timeout 120s
		}
	}
}
CADDY

if ! caddy validate --config "$caddy_file" --adapter caddyfile; then
  cp "$caddy_backup" "$caddy_file"
  fail "Caddy validation failed; restored $caddy_file from backup"
fi
if ! systemctl reload caddy; then
  cp "$caddy_backup" "$caddy_file"
  systemctl reload caddy || true
  fail "Caddy reload failed; restored $caddy_file from backup"
fi

printf '\nDeployment started. Caddy will obtain TLS automatically for https://%s.\n' "$domain"
printf 'Complete the shared Lark identity setup in a real terminal:\n'
printf '  cd %q\n' "$base_dir"
printf '  sudo docker compose exec mcp lark-cli config init --new\n'
printf '  sudo docker compose exec mcp lark-cli auth login --domain all --no-wait --json\n'
printf 'Then open verification_url, and finish with:\n'
printf "  sudo docker compose exec mcp lark-cli auth login --device-code '<device_code>'\n"
printf '  sudo docker compose exec mcp lark-cli auth status --json --verify\n'
printf 'Finally verify: curl -i https://%s/mcp\n' "$domain"
