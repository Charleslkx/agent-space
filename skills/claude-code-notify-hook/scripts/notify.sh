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
#   通知投递: macOS terminal-notifier/osascript；WSL Windows Toast；原生 Linux 仅飞书
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
FEISHU_ENV="${FEISHU_ENV:-$HOME/.claude/feishu-agent.env}"

dbg() { [ "${NOTIFY_DEBUG:-0}" = "1" ] && echo "[notify] $*" >&2; return 0; }

is_wsl() {
  [ "$(uname -s 2>/dev/null)" = "Linux" ] || return 1
  [ -n "${WSL_INTEROP:-}" ] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null
}

is_claude_desktop() {
  [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "claude-desktop" ]
}

local_notifications_enabled() {
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

# 飞书卡片 webhook 兜底：仅在 IM API 失败或显式配置 LARK_WEBHOOK_URL 时使用。
# 自定义 bot webhook 收不到按钮回调，且本设计只做通知不做交互，故恒为「仅展示」卡片。
lark_notify() {
  [ -n "${LARK_WEBHOOK_URL:-}" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  local agent="$1" project="$2" content="$3" kind="${4:-done}"
  python3 - "$agent" "$project" "$content" "$kind" <<'PY' >/dev/null 2>&1 || true
import base64, hashlib, hmac, json, os, sys, time, urllib.request
agent, project, content, kind = sys.argv[1:5]
if kind == "approval":
    title, template = f"⚠️ {agent} · 需要注意", "orange"
else:
    title, template = f"🤖 {agent} · 任务完成", "blue"
ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
card = {
    "schema": "2.0",
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
    "body": {
        "direction": "vertical",
        "padding": "12px 12px 12px 12px",
        "elements": [
            {
                "tag": "markdown",
                "content": f"**Agent**\n{agent}\n\n**Project**\n{project}\n\n**Content**\n{content}",
                "text_align": "left",
                "text_size": "normal_v2",
            },
            {
                "tag": "markdown",
                "content": f"<font color='grey'>🕒 {ts}</font>",
                "text_align": "left",
                "text_size": "normal_v2",
            },
        ],
    },
}
payload = {"msg_type": "interactive", "card": card}
secret = os.environ.get("LARK_WEBHOOK_SECRET", "").strip()
if secret:
    t = str(int(time.time()))
    payload["timestamp"] = t
    payload["sign"] = base64.b64encode(
        hmac.new(f"{t}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()
data = json.dumps(payload, ensure_ascii=False).encode()
req = urllib.request.Request(
    os.environ["LARK_WEBHOOK_URL"], data=data,
    headers={"Content-Type": "application/json"}, method="POST",
)
urllib.request.urlopen(req, timeout=5).read()
PY
}

# 飞书卡片主通道：自带 IM API 发送（无 codex 依赖），发纯通知卡片（无按钮）。
# 读取 $FEISHU_ENV 里的 FEISHU_APP_ID/SECRET/HOME_CHANNEL，取 tenant token 后投递。
# kind=done 绿色「任务完成」；kind=approval 橙色「需要授权」。
send_feishu_card() {
  local kind="$1" project="$2" content="$3"
  [ -f "$FEISHU_ENV" ] || { dbg "飞书 env 不存在: $FEISHU_ENV"; return 1; }
  command -v python3 >/dev/null 2>&1 || { dbg "无 python3，跳过 IM API"; return 1; }
  python3 - "$FEISHU_ENV" "$TITLE" "$project" "$content" "$kind" <<'PY' 2>/dev/null
import json, sys, time, urllib.request

env_path, agent, project, content, kind = sys.argv[1:6]
env = {}
try:
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
except OSError:
    sys.exit(1)

app_id, app_secret = env.get("FEISHU_APP_ID"), env.get("FEISHU_APP_SECRET")
receive_id = env.get("FEISHU_HOME_CHANNEL") or env.get("FEISHU_APPROVAL_RECEIVE_ID")
receive_id_type = env.get("FEISHU_APPROVAL_RECEIVE_ID_TYPE", "chat_id")
if not (app_id and app_secret and receive_id):
    sys.exit(1)
base = "https://open.larksuite.com" if env.get("FEISHU_DOMAIN") in {"lark", "larksuite"} else "https://open.feishu.cn"

def post(url, payload, headers=None):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

tok = post(
    f"{base}/open-apis/auth/v3/tenant_access_token/internal",
    {"app_id": app_id, "app_secret": app_secret},
)
if tok.get("code") != 0:
    sys.exit(1)

if kind == "approval":
    title, template = f"⚠️ {agent} · 需要注意", "orange"
else:
    title, template = f"🤖 {agent} · 任务完成", "blue"
ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
card = {
    "schema": "2.0",
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
    "body": {
        "direction": "vertical",
        "padding": "12px 12px 12px 12px",
        "elements": [
            {
                "tag": "markdown",
                "content": f"**Agent**\n{agent}\n\n**Project**\n{project}\n\n**Content**\n{content}",
                "text_align": "left",
                "text_size": "normal_v2",
            },
            {
                "tag": "markdown",
                "content": f"<font color='grey'>🕒 {ts}</font>",
                "text_align": "left",
                "text_size": "normal_v2",
            },
        ],
    },
}
resp = post(
    f"{base}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
    {"receive_id": receive_id, "msg_type": "interactive",
     "content": json.dumps(card, ensure_ascii=False)},
    {"Authorization": f"Bearer {tok['tenant_access_token']}"},
)
sys.exit(0 if resp.get("code") == 0 else 1)
PY
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
  # ponytail: Desktop 下多会话共用一个 bundle id，app 级焦点分不清是哪个会话，直接放弃判断
  is_claude_desktop && { dbg "Claude Desktop：跳过焦点检查"; return 1; }
  local owner="${__CFBundleIdentifier:-}" front
  [ -n "$owner" ] || { dbg "无 __CFBundleIdentifier，跳过焦点检查"; return 1; }
  front="$(front_bundle_id 2>/dev/null || true)"
  dbg "owner=$owner front=$front"
  [ -n "$front" ] && [ "$front" = "$owner" ]
}

# 投递通知：三级降级，每级失败都落到下一级
deliver() {
  local body="$1" subtitle="$2" owner="${__CFBundleIdentifier:-}"

  if ! local_notifications_enabled; then
    dbg "原生 Linux：跳过本地通知，仅发送飞书"
    return 0
  fi

  if is_wsl; then
    command -v powershell.exe >/dev/null 2>&1 || { dbg "WSL 未找到 powershell.exe"; return 0; }
    local ps_title ps_subtitle ps_body script
    ps_title="$(printf '%s' "$TITLE" | sed "s/'/''/g")"
    ps_subtitle="$(printf '%s' "$subtitle" | sed "s/'/''/g")"
    ps_body="$(printf '%s' "$body" | sed "s/'/''/g")"
    script="[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > \$null; [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime] > \$null; \$x=New-Object Windows.Data.Xml.Dom.XmlDocument; \$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>\$([System.Security.SecurityElement]::Escape('$ps_title'))</text><text>\$([System.Security.SecurityElement]::Escape('$ps_subtitle'))</text><text>\$([System.Security.SecurityElement]::Escape('$ps_body'))</text></binding></visual></toast>\"); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ClaudeCode').Show((New-Object Windows.UI.Notifications.ToastNotification \$x))"
    ( powershell.exe -NoProfile -NonInteractive -Command "$script" >/dev/null 2>&1 & )
    dbg "投递: WSL Windows Toast"
    return 0
  fi

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
  if send_feishu_card done "$project" "$1"; then
    dbg "飞书卡片已发送 (done)"
  else
    dbg "IM API 卡片失败，退回 webhook 兜底"
    ( lark_notify "$TITLE" "$project" "$1" done ) &
    disown 2>/dev/null || true
  fi
}

approval_notify() {
  if [ "${NOTIFY_FORCE:-0}" != "1" ] && is_focused_on_session; then
    dbg "焦点在会话窗口，静默"
    return 0
  fi
  local project
  project="$(project_name)"
  deliver "$1" "$project"
  if send_feishu_card approval "$project" "$1"; then
    dbg "飞书卡片已发送 (approval)"
  else
    dbg "IM API 卡片失败，退回 webhook 兜底"
    ( lark_notify "$TITLE" "$project" "$1" approval ) &
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
