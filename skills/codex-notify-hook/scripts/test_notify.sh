#!/usr/bin/env bash
set -eu

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_CAPTURE"' >"$tmp/bin/terminal-notifier"
chmod +x "$tmp/bin/terminal-notifier"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_FEISHU_CAPTURE"' >"$tmp/feishu-send"
chmod +x "$tmp/feishu-send"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' >"$tmp/bin/uname"
chmod +x "$tmp/bin/uname"

printf '%s' '{"hook_event_name":"Stop","cwd":"/tmp/demo-project"}' \
  | PATH="$tmp/bin:/opt/homebrew/bin:/usr/bin:/bin" \
    NOTIFY_CAPTURE="$tmp/notification" \
    NOTIFY_FEISHU_CAPTURE="$tmp/feishu" \
    FEISHU_NOTIFICATION_SEND="$tmp/feishu-send" \
    FEISHU_NOTIFICATION_PYTHON=/bin/bash \
    CODEX_INTERNAL_ORIGINATOR_OVERRIDE= \
    __CFBundleIdentifier= \
    NOTIFY_FORCE=1 \
    bash "$(dirname "$0")/notify.sh" stop

grep -Fq -- '-subtitle demo-project' "$tmp/notification"
grep -Fq -- '-message 任务已完成' "$tmp/notification"
grep -Fq -- '--notification' "$tmp/feishu"

printf '%s' '{"hook_event_name":"Stop","cwd":"/tmp/demo-project"}' \
  | PATH="$tmp/bin:/opt/homebrew/bin:/usr/bin:/bin" \
    NOTIFY_CAPTURE="$tmp/desktop-notification" \
    NOTIFY_FEISHU_CAPTURE="$tmp/desktop-feishu" \
    FEISHU_NOTIFICATION_SEND="$tmp/feishu-send" \
    FEISHU_NOTIFICATION_PYTHON=/bin/bash \
    CODEX_INTERNAL_ORIGINATOR_OVERRIDE='Codex Desktop' \
    __CFBundleIdentifier=com.openai.codex \
    NOTIFY_FORCE=1 \
    bash "$(dirname "$0")/notify.sh" stop

[ ! -e "$tmp/desktop-notification" ]
grep -Fq -- '--notification' "$tmp/desktop-feishu"
printf '%s\n' 'ok: Stop notification delivered'
