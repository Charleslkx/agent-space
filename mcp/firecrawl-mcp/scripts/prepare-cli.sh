#!/usr/bin/env bash
# First install resolves latest; existing deployments retain their resolved version.
set -euo pipefail
cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
record=deploy/firecrawl-cli.version
mode=${1:-prepare}
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
bash ../scripts/check-python.sh --runtime >&2
valid() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
latest() {
  curl --proto '=https' --tlsv1.2 -fsSL https://api.github.com/repos/firecrawl/cli/releases/latest \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert not d.get("draft") and not d.get("prerelease"); print(d["tag_name"].removeprefix("v"))'
}
case "$mode" in
  --latest) version=$(latest); valid "$version" || fail 'invalid latest release'; printf '%s\n' "$version"; exit ;;
  --use) version=${2:?provide the installed or explicitly chosen version} ;;
  prepare)
    if [ -f "$record" ]; then
      version=$(cat "$record")
      valid "$version" || fail 'invalid local CLI version record; inspect it before rebuilding'
      printf '%s\n' "$version"
      exit
    fi
    command -v docker >/dev/null || fail 'Docker is required to check for an existing deployment'
    docker info >/dev/null 2>&1 || fail 'Docker daemon unavailable; cannot safely detect an existing deployment'
    container=$(docker compose ps --all -q mcp)
    if [ -n "$container" ]; then
      output=$(docker compose exec -T mcp firecrawl --version) || fail 'cannot read existing CLI; start the container or record its known version with --use VERSION'
      version=$(printf '%s' "$output" | python3 -c 'import re,sys; m=re.search(r"\b\d+\.\d+\.\d+\b",sys.stdin.read()); assert m; print(m.group())')
    else
      version=$(latest)
    fi
    ;;
  *) fail 'usage: scripts/prepare-cli.sh [--latest|--use VERSION]' ;;
esac
valid "$version" || fail 'version must be a stable numeric release'
mkdir -p deploy
temporary=$(mktemp deploy/.lark-cli-version.XXXXXX)
trap 'rm -f "$temporary"' EXIT
printf '%s\n' "$version" > "$temporary"
chmod 644 "$temporary"
mv "$temporary" "$record"
printf '%s\n' "$version"
