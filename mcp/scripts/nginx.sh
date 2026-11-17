#!/usr/bin/env bash
# Render one service's templates. install modes validate and roll back on failure.
set -euo pipefail
service=${1:?service required}
mode=${2:-render}
domain=${DOMAIN:?set DOMAIN to the full public hostname}
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ ${#domain} -le 253 && "$domain" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] || fail 'invalid DOMAIN: use a lowercase hostname without scheme, port or path'
case "$service" in brave-search-mcp|firecrawl-mcp|exa-mcp|lark-cli-mcp|lark-doc) ;; *) fail 'unknown service' ;; esac
case "$mode" in render|bootstrap|tls) ;; *) fail 'mode must be render, bootstrap or tls' ;; esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stem=$service
[ "$service" != lark-cli-mcp ] || stem=lark-mcp
suffix=.nginx.conf
[ "$mode" != bootstrap ] || suffix=.bootstrap.nginx.conf
render() {
  # Replace the complete Lark hostname first. Do not substitute nginx's $variables.
  sed -e "s/lark\.YOUR_BASE_DOMAIN/$domain/g" -e "s/YOUR_DOMAIN/$domain/g" "$root/$service/deploy/$stem$suffix"
}
if [ "$mode" = render ]; then render; exit; fi
[ "$EUID" -eq 0 ] || fail 'install modes require root'
site=/etc/nginx/sites-available/$service
link=/etc/nginx/sites-enabled/$service
if [ "$mode" = bootstrap ] && { [ -e "$site" ] || [ -L "$link" ]; }; then
  fail 'site already exists; preserve the existing TLS site and use tls after certificate renewal'
fi
if [ -e "$link" ] || [ -L "$link" ]; then
  [ -L "$link" ] && [ "$(readlink "$link")" = "$site" ] || fail 'site link is managed elsewhere'
fi
if [ "$mode" = tls ]; then
  [ -s "/etc/letsencrypt/live/$domain/fullchain.pem" ] || fail 'issue the certificate before installing TLS config'
  [ -s "/etc/letsencrypt/live/$domain/privkey.pem" ] || fail 'certificate private key is missing'
fi
backup=$(mktemp)
staged=$(mktemp)
trap 'rm -f "$backup" "$staged"' EXIT
render > "$staged"
had_site=0
had_link=0
[ ! -e "$site" ] || { cp -p "$site" "$backup"; had_site=1; }
[ ! -L "$link" ] || had_link=1
rollback() {
  if [ "$had_site" = 1 ]; then cp -p "$backup" "$site"; else rm -f "$site"; fi
  [ "$had_link" = 1 ] || rm -f "$link"
}
trap 'rollback' ERR
install -m 644 "$staged" "$site"
[ "$had_link" = 1 ] || ln -s "$site" "$link"
if ! nginx -t; then rollback; fail 'nginx validation failed; restored prior files'; fi
if ! systemctl reload nginx; then rollback; fail 'nginx reload failed; restored prior files'; fi
