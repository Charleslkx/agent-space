#!/usr/bin/env bash
# Claude Code 通知 hook —— 带逐级降级，任一依赖缺失/失效都不影响核心通知
#
# 用法: notify.sh <event>
#   notification -> Notification 事件（需要授权/输入等）
#   stop         -> Stop 事件（任务完成）
# 事件 JSON 通过 stdin 传入。
#
# 降级链:
#   消息解析: jq  ->  纯文本 grep/sed  ->  通用默认文案
#   焦点识别: lsappinfo + __CFBundleIdentifier  ->  缺任一则跳过(宁可多弹)
#   通知投递: terminal-notifier(可点击跳转) -> osascript(原生) -> 终端响铃
#
# 调试: NOTIFY_DEBUG=1 打印决策；NOTIFY_FORCE=1 跳过焦点检查强制弹出。
# 提示音: /System/Library/Sounds/ 下任意名字（Glass/Ping/Hero/Submarine...）。
#
# 故意不使用 set -e/-u/pipefail：通知 hook 的首要目标是“永不因自身报错而中断”，
# 所有外部命令均显式守卫并以 || true 兜底，结尾恒定 exit 0。

EVENT="${1:-notification}"
SOUND="Glass"
TITLE="ClaudeCode"
DEFAULT_MSG="需要你的关注"
FEISHU_APPROVAL_SEND="${FEISHU_APPROVAL_SEND:-/Users/charles/.codex/hooks/feishu_send_approval.py}"
FEISHU_APPROVAL_PYTHON="${FEISHU_APPROVAL_PYTHON:-/Users/charles/Nutstore/agent-space/.venv/bin/python}"

dbg() { [ "${NOTIFY_DEBUG:-0}" = "1" ] && echo "[notify] $*" >&2; return 0; }

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
  printf 'claude-%s-%s' "$(date +%s 2>/dev/null || printf 0)" "$$"
}

send_feishu_approval() {
  [ -f "$FEISHU_APPROVAL_SEND" ] || { dbg "飞书审批发送脚本不存在: $FEISHU_APPROVAL_SEND"; return 1; }
  [ -x "$FEISHU_APPROVAL_PYTHON" ] || { dbg "Python 不存在: $FEISHU_APPROVAL_PYTHON"; return 1; }
  local id="$1" project="$2" content="$3"
  "$FEISHU_APPROVAL_PYTHON" "$FEISHU_APPROVAL_SEND" --agent "$TITLE" --approval-id "$id" --project "$project" --content "$content" >/dev/null 2>&1
}

# 解析 message：优先 jq，失败退到纯文本抓取，再退到空
extract_message() {
  local out=""
  if command -v jq >/dev/null 2>&1; then
    out="$(printf '%s' "$INPUT" | jq -r '.message // empty' 2>/dev/null || true)"
  fi
  if [ -z "$out" ]; then
    out="$( { printf '%s' "$INPUT" \
      | grep -o '"message"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 | sed 's/.*:[[:space:]]*"//; s/"$//'; } 2>/dev/null || true)"
  fi
  printf '%s' "$out"
}

# 当前最前应用 bundle id（lsappinfo 缺失/失败则返回非 0）
front_bundle_id() {
  command -v lsappinfo >/dev/null 2>&1 || return 1
  local asn; asn="$(lsappinfo front 2>/dev/null || true)"
  [ -n "$asn" ] || return 1
  lsappinfo info -only bundleid "$asn" 2>/dev/null | cut -d'"' -f4
}

# 焦点是否在承载本会话的 app（任一信号缺失 -> 返回 1，即“不静默”，宁可多弹）
is_focused_on_session() {
  local owner="${__CFBundleIdentifier:-}" front
  [ -n "$owner" ] || { dbg "无 __CFBundleIdentifier，跳过焦点检查"; return 1; }
  front="$(front_bundle_id 2>/dev/null || true)"
  dbg "owner=$owner front=$front"
  [ -n "$front" ] && [ "$front" = "$owner" ]
}

# 投递通知：三级降级，每级失败都落到下一级
deliver() {
  local body="$1" subtitle="$2" owner="${__CFBundleIdentifier:-}"

  # 1) terminal-notifier：支持点击跳转 + app 图标，后台投递不阻塞
  if command -v terminal-notifier >/dev/null 2>&1; then
    local args=(-title "$TITLE" -subtitle "$subtitle" -message "$body" -sound "$SOUND")
    # 不传 -sender/-activate：macOS 12+ 已限制伪装 sender，会导致通知被静默丢弃
    ( terminal-notifier "${args[@]}" >/dev/null 2>&1 || true ) &
    disown 2>/dev/null || true
    dbg "投递: terminal-notifier (owner=${owner:-无})"
    return 0
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
  ( lark_notify "$TITLE" "$project" "$1" ) &
  disown 2>/dev/null || true
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

case "$EVENT" in
  notification)
    msg="$(extract_message)"
    [ -n "$msg" ] || { msg="$DEFAULT_MSG"; dbg "message 为空，用默认文案"; }
    # 屏蔽任务结束后的空闲催促（与 Stop 通知重复）
    if printf '%s' "$msg" | grep -qi "waiting for your input" 2>/dev/null; then
      dbg "空闲催促，静默"
      exit 0
    fi
    approval_notify "$msg"
    ;;
  stop)
    notify "任务已完成"
    ;;
  *)
    dbg "未知事件: $EVENT"
    ;;
esac

exit 0
