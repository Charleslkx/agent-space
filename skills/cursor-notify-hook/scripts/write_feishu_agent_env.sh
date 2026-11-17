#!/usr/bin/env bash
# Enter credentials locally; never edit them into this source file.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export NOTIFY_SKILL_NAME=cursor-notify-hook
bash "$script_dir/check-python.sh" --runtime >&2
exec python3 "$script_dir/create_feishu_agent_app.py" --configure "$@"
