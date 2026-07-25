#!/usr/bin/env bash
# Check for (and optionally apply) a newer firecrawl CLI release, with
# automatic rollback if the new build or its smoke test fails.
#
# Usage:
#   scripts/update-cli.sh              # apply latest release
#   scripts/update-cli.sh --dry-run    # only report, change nothing
#   scripts/update-cli.sh 1.19.28      # apply a specific version
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DOCKERFILE=Dockerfile
REPO=firecrawl/cli
DRY_RUN=0
TARGET_VERSION=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) TARGET_VERSION="$arg" ;;
  esac
done

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
need curl
need docker
need sed

current_version() {
  sed -n 's/^ARG FIRECRAWL_VERSION=\(.*\)$/\1/p' "$DOCKERFILE"
}

latest_version() {
  curl -fsSL -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${REPO}/releases/latest" \
    | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -n1
}

CURRENT=$(current_version)
[ -n "$CURRENT" ] || fail "could not read ARG FIRECRAWL_VERSION from $DOCKERFILE"

NEW="${TARGET_VERSION:-$(latest_version)}"
[ -n "$NEW" ] || fail "could not determine latest firecrawl/cli release"

printf 'current: %s\nlatest:  %s\n' "$CURRENT" "$NEW"

if [ "$CURRENT" = "$NEW" ]; then
  printf 'already up to date\n'
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  printf 'dry run: would upgrade %s -> %s (no changes made)\n' "$CURRENT" "$NEW"
  exit 0
fi

# Confirm the target release actually publishes the assets we need before
# touching anything.
for asset in "firecrawl-linux-x64.tar.gz" "firecrawl-linux-arm64.tar.gz" "checksums.txt"; do
  url="https://github.com/${REPO}/releases/download/v${NEW}/${asset}"
  curl -fsSL -o /dev/null --head "$url" || fail "release v${NEW} is missing asset: $asset"
done

BACKUP=$(mktemp)
cp "$DOCKERFILE" "$BACKUP"
trap 'rm -f "$BACKUP"' EXIT

rollback() {
  printf 'rolling back to %s after failure at: %s\n' "$CURRENT" "$1" >&2
  cp "$BACKUP" "$DOCKERFILE"
  docker compose build mcp >&2 || printf 'warning: rollback build also failed; Dockerfile has been restored to %s, apply the fix manually\n' "$CURRENT" >&2
  docker compose up -d --force-recreate mcp >&2 || true
  exit 1
}

sed -i.bak "s/^ARG FIRECRAWL_VERSION=.*/ARG FIRECRAWL_VERSION=${NEW}/" "$DOCKERFILE"
rm -f "${DOCKERFILE}.bak"

docker compose build mcp || rollback "docker compose build"

docker compose up -d --force-recreate mcp || rollback "docker compose up"

printf 'waiting for container to become healthy...\n'
deadline=$((SECONDS + 60))
until [ "$(docker compose ps -q mcp | xargs -r docker inspect -f '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ]; do
  [ "$SECONDS" -lt "$deadline" ] || rollback "healthcheck timeout"
  sleep 2
done

docker compose exec -T mcp firecrawl --version || rollback "version smoke test"
docker compose exec -T mcp firecrawl search "firecrawl cli" --limit 1 --json >/dev/null \
  || printf 'warning: live search smoke test failed (check FIRECRAWL_API_KEY / credits) -- verify manually, this alone does not trigger a rollback\n' >&2

trap - EXIT
rm -f "$BACKUP"
printf 'upgraded %s -> %s\n' "$CURRENT" "$NEW"
