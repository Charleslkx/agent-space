#!/usr/bin/env bash
set -eu

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/codex"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_CAPTURE"' >"$tmp/bin/terminal-notifier"
chmod +x "$tmp/bin/terminal-notifier"
printf '%s\n' 'approvals_reviewer = "auto_review"' >"$tmp/codex/config.toml"

printf '%s' '{"hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"git push origin main"}}' \
  | PATH="$tmp/bin:/opt/homebrew/bin:/usr/bin:/bin" \
    CODEX_HOME="$tmp/codex" \
    FEISHU_APPROVAL_SEND="$tmp/missing" \
    NOTIFY_CAPTURE="$tmp/notification" \
    NOTIFY_FORCE=1 \
    bash "$(dirname "$0")/notify.sh" permission-request

grep -Fq -- '-message 需要授权: Bash — git push origin main' "$tmp/notification"
printf '%s\n' 'ok: auto_review config does not suppress PermissionRequest notification'
