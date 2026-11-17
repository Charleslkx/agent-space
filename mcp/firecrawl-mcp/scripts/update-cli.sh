#!/usr/bin/env bash
# Explicit upgrade only. Normal code changes/rebuilds retain the local CLI version.
set -euo pipefail
cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
bash ../scripts/check-python.sh --setup >&2
command -v docker >/dev/null || fail 'Docker is required'
docker info >/dev/null 2>&1 || fail 'cannot access Docker; run with sudo'
target=${1:-}
dry_run=0
[ "$target" != --dry-run ] || { dry_run=1; target=; }
container=$(docker compose ps --all -q mcp)
[ -n "$container" ] || fail 'no existing deployment; follow the first-install guide'
output=$(docker compose exec -T mcp firecrawl --version) || fail 'start the installed service before upgrading'
current=$(printf '%s' "$output" | python3 -c 'import re,sys; m=re.search(r"\b\d+\.\d+\.\d+\b",sys.stdin.read()); assert m; print(m.group())')
[ -n "$target" ] || target=$(bash scripts/prepare-cli.sh --latest)
[[ "$target" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'target must be a stable numeric version'
printf 'installed: %s\ntarget:    %s\n' "$current" "$target"
[ "$dry_run" = 0 ] || { printf 'dry run: nothing changed\n'; exit; }
[ "$target" != "$current" ] || { printf 'already up to date\n'; exit; }
for asset in "firecrawl-linux-x64.tar.gz" "firecrawl-linux-arm64.tar.gz" "checksums.txt"; do
  curl --proto '=https' --tlsv1.2 -fsSL --head -o /dev/null "https://github.com/firecrawl/cli/releases/download/v${target}/${asset}" || fail "release is missing $asset"
done
old_image=$(docker inspect -f '{{.Image}}' "$container")
image_ref=$(docker inspect -f '{{.Config.Image}}' "$container")
backup_tag="firecrawl-mcp-rollback:upgrade-$$"
docker image tag "$old_image" "$backup_tag"
# The running version is the authoritative rollback baseline, including legacy installs.
bash scripts/prepare-cli.sh --use "$current" >/dev/null
rollback() {
  printf 'upgrade failed; restoring installed CLI %s\n' "$current" >&2
  bash scripts/prepare-cli.sh --use "$current" >/dev/null
  docker image tag "$backup_tag" "$image_ref"
  docker compose up -d --no-build --force-recreate --wait --wait-timeout 180 mcp >&2 || true
}
trap rollback ERR
bash scripts/prepare-cli.sh --use "$target" >/dev/null
python3 ../scripts/prepare-python.py firecrawl-mcp
docker compose build mcp
docker compose up -d --no-build --force-recreate --wait --wait-timeout 180 mcp
output=$(docker compose exec -T mcp firecrawl --version)
actual=$(printf '%s' "$output" | python3 -c 'import re,sys; m=re.search(r"\b\d+\.\d+\.\d+\b",sys.stdin.read()); assert m; print(m.group())')
[ "$actual" = "$target" ]
trap - ERR
printf 'upgraded %s -> %s; previous image retained as %s\n' "$current" "$target" "$backup_tag"
