#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${HOME}/.hermes/scripts"
DEST_FILE="${DEST_DIR}/feishu_base_autumn_jobs_notify.py"

mkdir -p "${DEST_DIR}"
cp "${SCRIPT_DIR}/feishu_base_autumn_jobs_notify.py" "${DEST_FILE}"
chmod +x "${DEST_FILE}"

echo "installed: ${DEST_FILE}"
