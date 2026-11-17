#!/usr/bin/env bash
# Compatibility entry point; supports both Ubuntu and Debian.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [ -z "${DOMAIN:-}" ] && [ -n "${BASE_DOMAIN:-}" ]; then
  export DOMAIN="lark.$BASE_DOMAIN"
fi
exec bash "$script_dir/../../scripts/linux.sh" lark-cli-mcp 8768 "${1:-check}"
