#!/usr/bin/env bash
set -eu

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_CAPTURE"' >"$tmp/bin/osascript"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' >"$tmp/bin/uname"
chmod +x "$tmp/bin/osascript" "$tmp/bin/uname"

echo '{}' | PATH="$tmp/bin:/usr/bin:/bin" NOTIFY_CAPTURE="$tmp/cli" NOTIFY_FORCE=1 CLAUDE_CODE_ENTRYPOINT= __CFBundleIdentifier= FEISHU_ENV=/missing bash "$(dirname "$0")/notify.sh" stop
grep -Fq '任务已完成' "$tmp/cli"

echo '{}' | PATH="$tmp/bin:/usr/bin:/bin" NOTIFY_CAPTURE="$tmp/desktop" NOTIFY_FORCE=1 __CFBundleIdentifier=com.anthropic.claudefordesktop FEISHU_ENV=/missing bash "$(dirname "$0")/notify.sh" stop
[ ! -e "$tmp/desktop" ]
echo '{}' | PATH="$tmp/bin:/usr/bin:/bin" NOTIFY_CAPTURE="$tmp/entrypoint" NOTIFY_FORCE=1 CLAUDE_CODE_ENTRYPOINT=claude-desktop __CFBundleIdentifier= FEISHU_ENV=/missing bash "$(dirname "$0")/notify.sh" stop
[ ! -e "$tmp/entrypoint" ]
printf '%s\n' 'ok: CLI sends local notification; Claude Desktop skips it'
