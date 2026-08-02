#!/usr/bin/env bash
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target=${1:-}
dry_run=0
[ "$target" = --dry-run ] && { dry_run=1; target=""; }

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need curl
need docker
need sed
docker info >/dev/null 2>&1 || fail 'cannot access the Docker daemon; run this script with sudo'

current=$(sed -n 's/^ARG LARK_CLI_VERSION=\(.*\)$/\1/p' Dockerfile)
[ -n "$current" ] || fail 'cannot read LARK_CLI_VERSION from Dockerfile'
if [ -z "$target" ]; then
  target=$(curl -fsSL -H 'Accept: application/vnd.github+json' \
    https://api.github.com/repos/larksuite/cli/releases/latest \
    | sed -n 's/.*"tag_name": *"v\{0,1\}\([0-9][0-9.]*\)".*/\1/p' | head -n1)
fi
printf '%s' "$target" | grep -Eq '^[0-9]+(\.[0-9]+)+$' || fail 'target version must be a stable numeric version'
printf 'current: %s\nlatest:  %s\n' "$current" "$target"
[ "$current" = "$target" ] && { printf 'already up to date\n'; exit 0; }
[ "$dry_run" = 1 ] && { printf 'dry run: no files changed\n'; exit 0; }

for asset in "lark-cli-${target}-linux-amd64.tar.gz" "lark-cli-${target}-linux-arm64.tar.gz" checksums.txt; do
  curl -fsSL --head -o /dev/null "https://github.com/larksuite/cli/releases/download/v${target}/${asset}" \
    || fail "release v${target} is missing $asset"
done

backup_dir=$(mktemp -d)
cp Dockerfile "$backup_dir/Dockerfile"
cp compose.yaml "$backup_dir/compose.yaml"
trap 'rm -rf "$backup_dir"' EXIT
rollback() {
  printf 'upgrade failed at %s; restoring %s\n' "$1" "$current" >&2
  cp "$backup_dir/Dockerfile" Dockerfile
  cp "$backup_dir/compose.yaml" compose.yaml
  docker compose build mcp >&2 || true
  docker compose up -d --force-recreate mcp >&2 || true
  exit 1
}

sed -i.bak "s/^ARG LARK_CLI_VERSION=.*/ARG LARK_CLI_VERSION=${target}/" Dockerfile
rm -f Dockerfile.bak
sed -i.bak "s/LARK_CLI_VERSION: \"${current}\"/LARK_CLI_VERSION: \"${target}\"/" compose.yaml
rm -f compose.yaml.bak
grep -q "^ARG LARK_CLI_VERSION=${target}$" Dockerfile || rollback version-pin
grep -q "LARK_CLI_VERSION: \"${target}\"" compose.yaml || rollback compose-version-pin
docker compose build mcp || rollback build
docker compose up -d --force-recreate mcp || rollback start

deadline=$((SECONDS + 60))
until [ "$(docker compose ps -q mcp | xargs -r docker inspect -f '{{.State.Health.Status}}' 2>/dev/null)" = healthy ]; do
  [ "$SECONDS" -lt "$deadline" ] || rollback healthcheck
  sleep 2
done
docker compose exec -T mcp lark-cli --version | grep -q "$target" || rollback version-check
trap - EXIT
rm -rf "$backup_dir"
printf 'upgraded %s -> %s\n' "$current" "$target"
