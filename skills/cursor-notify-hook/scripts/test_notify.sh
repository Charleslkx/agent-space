#!/usr/bin/env bash
set -eu

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >"$NOTIFY_CAPTURE"' >"$tmp/bin/osascript"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' >"$tmp/bin/uname"
chmod +x "$tmp/bin/osascript" "$tmp/bin/uname"
SH="$(cd "$(dirname "$0")" && pwd)/notify.sh"

run_stop() {
  local capture="$1" payload="$2"
  PATH="$tmp/bin:/usr/bin:/bin" \
    NOTIFY_CAPTURE="$capture" \
    NOTIFY_FORCE=1 \
    __CFBundleIdentifier= \
    FEISHU_ENV=/missing \
    bash "$SH" stop >/dev/null <<<"$payload"
}

run_stop "$tmp/completed" '{"hook_event_name":"stop","status":"completed","workspace_roots":["/tmp/demo-project"]}'
grep -Fq '任务已完成' "$tmp/completed"
grep -Fq 'demo-project' "$tmp/completed"

run_stop "$tmp/default" '{"hook_event_name":"stop","cwd":"/tmp/other-project"}'
grep -Fq '任务已完成' "$tmp/default"
grep -Fq 'other-project' "$tmp/default"

run_stop "$tmp/aborted" '{"hook_event_name":"stop","status":"aborted","workspace_roots":["/tmp/demo-project"]}'
[ ! -e "$tmp/aborted" ]

run_stop "$tmp/error" '{"hook_event_name":"stop","status":"error","workspace_roots":["/tmp/demo-project"]}'
grep -Fq '任务异常结束' "$tmp/error"

stdout="$(printf '%s' '{"status":"completed","cwd":"/tmp/demo-project"}' \
  | PATH="$tmp/bin:/usr/bin:/bin" NOTIFY_CAPTURE="$tmp/stdout-check" \
    NOTIFY_FORCE=1 __CFBundleIdentifier= FEISHU_ENV=/missing \
    bash "$SH" stop)"
[ -z "$stdout" ]

printf '%s\n' 'ok: Cursor stop notification delivered'
