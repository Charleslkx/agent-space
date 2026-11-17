#!/usr/bin/env bash
# Store secrets interactively, outside the distributed Skill.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export NOTIFY_SKILL_NAME=codex-notify-hook
bash "$script_dir/check-python.sh" --runtime >&2
exec python3 "$script_dir/create_feishu_agent_app.py" --configure-webhook "$@"
