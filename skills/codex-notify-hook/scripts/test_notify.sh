#!/usr/bin/env bash
set -eu

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_CAPTURE"' >"$tmp/bin/terminal-notifier"
chmod +x "$tmp/bin/terminal-notifier"

printf '%s' '{"hook_event_name":"Stop","cwd":"/tmp/demo-project"}' \
  | PATH="$tmp/bin:/opt/homebrew/bin:/usr/bin:/bin" \
    NOTIFY_CAPTURE="$tmp/notification" \
    NOTIFY_FORCE=1 \
    bash "$(dirname "$0")/notify.sh" stop

grep -Fq -- '-subtitle demo-project' "$tmp/notification"
grep -Fq -- '-message 任务已完成' "$tmp/notification"
printf '%s\n' 'ok: Stop notification delivered'
