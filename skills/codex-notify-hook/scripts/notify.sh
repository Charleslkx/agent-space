#!/usr/bin/env bash
# Codex 通知 hook —— 带逐级降级，任一依赖缺失/失效都不影响核心通知
#
# 用法: notify.sh <event>
#   permission-request -> PermissionRequest 事件（需要授权）
#   stop               -> Stop 事件（任务完成/turn 结束）
# 事件 JSON 通过 stdin 传入（Codex hooks 系统），含 hook_event_name 等字段。
#
# 与 Claude Code notify hook 的对应关系:
#   Claude Code Notification (需要授权/输入) -> Codex PermissionRequest
#   Claude Code Stop (任务完成)              -> Codex Stop
#
# 降级链:
#   消息解析: jq  ->  纯文本 grep/sed  ->  通用默认文案
#   焦点识别: lsappinfo + __CFBundleIdentifier  ->  缺任一则跳过(宁可多弹)
#   通知投递: macOS terminal-notifier/osascript；WSL Windows Toast；原生 Linux 仅飞书
#
# 调试: NOTIFY_DEBUG=1 打印决策；NOTIFY_FORCE=1 跳过焦点检查强制弹出。
# 提示音: /System/Library/Sounds/ 下任意名字（Glass/Ping/Hero/Submarine...）。
#
# 故意不使用 set -e/-u/pipefail：通知 hook 的首要目标是"永不因自身报错而中断"，
# 所有外部命令均显式守卫并以 || true 兜底，结尾恒定 exit 0。

EVENT="${1:-}"
SOUND="Glass"
TITLE="Codex"
DEFAULT_MSG="需要你的关注"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
FEISHU_APPROVAL_SEND="${FEISHU_APPROVAL_SEND:-$SCRIPT_DIR/feishu_send_approval.py}"
FEISHU_APPROVAL_PYTHON="${FEISHU_APPROVAL_PYTHON:-python3}"
FEISHU_NOTIFICATION_SEND="${FEISHU_NOTIFICATION_SEND:-$FEISHU_APPROVAL_SEND}"
FEISHU_NOTIFICATION_PYTHON="${FEISHU_NOTIFICATION_PYTHON:-$FEISHU_APPROVAL_PYTHON}"
FEISHU_WEBHOOK_ENV="${FEISHU_WEBHOOK_ENV:-$HOME/.codex/feishu-webhook.env}"

dbg() { [ "${NOTIFY_DEBUG:-0}" = "1" ] && echo "[notify] $*" >&2; return 0; }

load_webhook_env() {
  [ -r "$FEISHU_WEBHOOK_ENV" ] || return 0
  while IFS='=' read -r key value; do
    case "$key" in
      LARK_WEBHOOK_URL|LARK_WEBHOOK_SECRET) export "$key=$value" ;;
    esac
  done < "$FEISHU_WEBHOOK_ENV"
}

load_webhook_env

is_wsl() {
  [ "$(uname -s 2>/dev/null)" = "Linux" ] || return 1
  [ -n "${WSL_INTEROP:-}" ] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null
}

is_codex_desktop() {
  [ "${CODEX_INTERNAL_ORIGINATOR_OVERRIDE:-}" = "Codex Desktop" ] && \
    [ "${__CFBundleIdentifier:-}" = "com.openai.codex" ]
}

local_notifications_enabled() {
  if is_codex_desktop; then
    return 1
  fi
  [ "$(uname -s 2>/dev/null)" = "Darwin" ] || is_wsl
}

# 一次性读入 stdin（事件 JSON），为空也不报错
INPUT="$(cat 2>/dev/null || true)"

project_name() {
  local dir="${NOTIFY_PROJECT_DIR:-}"
  if [ -z "$dir" ] && command -v jq >/dev/null 2>&1; then
    dir="$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace.current_dir // empty' 2>/dev/null || true)"
  fi
  [ -n "$dir" ] || dir="$PWD"
  basename "$dir"
}

lark_notify() {
  [ -n "${LARK_WEBHOOK_URL:-}" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  python3 - "$1" "$2" "$3" <<'PY' >/dev/null 2>&1 || true
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

agent, project, content = sys.argv[1:4]
payload = {
    "msg_type": "text",
    "content": {"text": f"Agent: {agent}\nProject: {project}\nContent: {content}"},
}
secret = os.environ.get("LARK_WEBHOOK_SECRET", "").strip()
if secret:
    ts = str(int(time.time()))
    payload["timestamp"] = ts
    payload["sign"] = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()
data = json.dumps(payload).encode()
req = urllib.request.Request(
    os.environ["LARK_WEBHOOK_URL"],
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
urllib.request.urlopen(req, timeout=5).read()
PY
}

approval_id() {
  printf 'codex-%s-%s' "$(date +%s 2>/dev/null || printf 0)" "$$"
}

send_feishu_approval() {
  [ -f "$FEISHU_APPROVAL_SEND" ] || { dbg "飞书审批发送脚本不存在: $FEISHU_APPROVAL_SEND"; return 1; }
  command -v "$FEISHU_APPROVAL_PYTHON" >/dev/null 2>&1 || { dbg "Python 不存在: $FEISHU_APPROVAL_PYTHON"; return 1; }
  local id="$1" project="$2" content="$3"
  "$FEISHU_APPROVAL_PYTHON" "$FEISHU_APPROVAL_SEND" --agent "$TITLE" --approval-id "$id" --project "$project" --content "$content" >/dev/null 2>&1
}

send_feishu_notification() {
  [ -f "$FEISHU_NOTIFICATION_SEND" ] || { dbg "飞书发送脚本不存在: $FEISHU_NOTIFICATION_SEND"; return 1; }
  command -v "$FEISHU_NOTIFICATION_PYTHON" >/dev/null 2>&1 || { dbg "Python 不存在: $FEISHU_NOTIFICATION_PYTHON"; return 1; }
  local id="$1" project="$2" content="$3"
  "$FEISHU_NOTIFICATION_PYTHON" "$FEISHU_NOTIFICATION_SEND" --notification --agent "$TITLE" --approval-id "$id" --project "$project" --content "$content" >/dev/null 2>&1
}

# 确定事件类型：优先用位置参数，其次从 stdin JSON 的 hook_event_name 推断
resolve_event() {
  if [ -n "$EVENT" ]; then
    printf '%s' "$EVENT"
    return 0
  fi
  local ev=""
  if command -v jq >/dev/null 2>&1; then
    ev="$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null || true)"
  fi
  if [ -z "$ev" ]; then
    ev="$( { printf '%s' "$INPUT" \
      | grep -o '"hook_event_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 | sed 's/.*:[[:space:]]*"//; s/"$//'; } 2>/dev/null || true)"
  fi
  case "$ev" in
    PermissionRequest) printf 'permission-request' ;;
    Stop) printf 'stop' ;;
    *) printf '%s' "$ev" ;;
  esac
}

# 用 jq 取嵌套字段，失败返回空
jq_field() {
  local filter="$1"
  command -v jq >/dev/null 2>&1 || return 1
  printf '%s' "$INPUT" | jq -r "$filter // empty" 2>/dev/null || true
}

# 构造授权类消息：tool_name + tool_input.description / tool_input.command
extract_approval_msg() {
  local tool desc cmd
  tool="$(jq_field '.tool_name' || true)"
  desc="$(jq_field '.tool_input.description' || true)"
  cmd="$(jq_field '.tool_input.command' || true)"
  if [ -n "$desc" ]; then
    printf '需要授权: %s — %s' "${tool:-工具}" "$desc"
  elif [ -n "$cmd" ]; then
    # 截断超长命令
    local short; short="$(printf '%s' "$cmd" | head -c 120)"
    printf '需要授权: %s — %s' "${tool:-工具}" "$short"
  elif [ -n "$tool" ]; then
    printf '需要授权: %s' "$tool"
  else
    printf '%s' "$DEFAULT_MSG"
  fi
}

# 当前最前应用 bundle id（lsappinfo 缺失/失败则返回非 0）
front_bundle_id() {
  command -v lsappinfo >/dev/null 2>&1 || return 1
  local asn; asn="$(lsappinfo front 2>/dev/null || true)"
  [ -n "$asn" ] || return 1
  lsappinfo info -only bundleid "$asn" 2>/dev/null | cut -d'"' -f4
}

# 焦点是否在承载本会话的 app（任一信号缺失 -> 返回 1，即"不静默"，宁可多弹）
is_focused_on_session() {
  local owner="${__CFBundleIdentifier:-}" front
  [ -n "$owner" ] || { dbg "无 __CFBundleIdentifier，跳过焦点检查"; return 1; }
  front="$(front_bundle_id 2>/dev/null || true)"
  dbg "owner=$owner front=$front"
  [ -n "$front" ] && [ "$front" = "$owner" ]
}

# 投递通知：三级降级，每级失败都落到下一级
deliver() {
  local body="$1" subtitle="$2"

  if ! local_notifications_enabled; then
    if is_codex_desktop; then
      dbg "Codex App：跳过本地通知，仅发送飞书"
    else
      dbg "原生 Linux：跳过本地通知，仅发送飞书"
    fi
    return 0
  fi

  if is_wsl; then
    command -v powershell.exe >/dev/null 2>&1 || { dbg "WSL 未找到 powershell.exe"; return 0; }
    local ps_title ps_subtitle ps_body script
    ps_title="$(printf '%s' "$TITLE" | sed "s/'/''/g")"
    ps_subtitle="$(printf '%s' "$subtitle" | sed "s/'/''/g")"
    ps_body="$(printf '%s' "$body" | sed "s/'/''/g")"
    script="[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > \$null; [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime] > \$null; \$x=New-Object Windows.Data.Xml.Dom.XmlDocument; \$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>\$([System.Security.SecurityElement]::Escape('$ps_title'))</text><text>\$([System.Security.SecurityElement]::Escape('$ps_subtitle'))</text><text>\$([System.Security.SecurityElement]::Escape('$ps_body'))</text></binding></visual></toast>\"); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Codex').Show((New-Object Windows.UI.Notifications.ToastNotification \$x))"
    ( powershell.exe -NoProfile -NonInteractive -Command "$script" >/dev/null 2>&1 & )
    dbg "投递: WSL Windows Toast"
    return 0
  fi

  # 1) terminal-notifier：同步试投，崩溃/非零退出立即降级
  #    macOS 26 (Tahoe) 上 terminal-notifier 已不维护会 Abort trap，必须检测崩溃才能降级
  if command -v terminal-notifier >/dev/null 2>&1; then
    if terminal-notifier -title "$TITLE" -subtitle "$subtitle" -message "$body" -sound "$SOUND" >/dev/null 2>&1; then
      dbg "投递: terminal-notifier"
      return 0
    fi
    dbg "terminal-notifier 失败/崩溃，继续降级"
  fi

  # 2) osascript：原生通知（点击无跳转）
  if command -v osascript >/dev/null 2>&1; then
    if osascript \
        -e 'on run argv' \
        -e "display notification (item 1 of argv) with title \"$TITLE\" subtitle \"$subtitle\" sound name \"$SOUND\"" \
        -e 'end run' \
        "$body" >/dev/null 2>&1; then
      dbg "投递: osascript"
      return 0
    fi
    dbg "osascript 失败，继续降级"
  fi

  # 3) 终端响铃：至少给个声音提示
  printf '\a' 2>/dev/null || true
  dbg "投递: 终端响铃 (兜底)"
  return 0
}

# 焦点检查 + 投递
notify() {
  if [ "${NOTIFY_FORCE:-0}" != "1" ] && is_focused_on_session; then
    dbg "焦点在会话窗口，静默"
    return 0
  fi
  local project; project="$(project_name)"
  deliver "$1" "$project"
  if send_feishu_notification "$(approval_id)" "$project" "$1"; then
    dbg "飞书完成通知已发送"
  else
    dbg "飞书应用通知失败，退回简单 webhook"
    ( lark_notify "$TITLE" "$project" "$1" ) &
    disown 2>/dev/null || true
  fi
}

approval_notify() {
  if [ "${NOTIFY_FORCE:-0}" != "1" ] && is_focused_on_session; then
    dbg "焦点在会话窗口，静默"
    return 0
  fi
  local project id
  project="$(project_name)"
  id="$(approval_id)"
  deliver "$1" "$project"
  if send_feishu_approval "$id" "$project" "$1"; then
    dbg "飞书通知已发送: $id"
  else
    dbg "飞书审批发送失败，退回简单 webhook"
    ( lark_notify "$TITLE" "$project" "$1" ) &
    disown 2>/dev/null || true
  fi
}
EVENT="$(resolve_event)"

case "$EVENT" in
  permission-request|PermissionRequest)
    msg="$(extract_approval_msg)"
    approval_notify "$msg"
    ;;
  stop|Stop)
    notify "任务已完成"
    ;;
  *)
    dbg "未知事件: $EVENT"
    ;;
esac

exit 0
