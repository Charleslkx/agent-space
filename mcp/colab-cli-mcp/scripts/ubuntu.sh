#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
exec bash "$root/scripts/linux.sh" colab-cli-mcp 8770 "${1:-check}"
