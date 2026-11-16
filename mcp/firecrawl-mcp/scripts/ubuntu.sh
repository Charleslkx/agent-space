#!/usr/bin/env bash
# Compatibility entry point; supports both Ubuntu and Debian.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$script_dir/../../scripts/linux.sh" firecrawl-mcp 8767 "${1:-check}"
