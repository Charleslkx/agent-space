---
name: cursor-notify-hook
description: >
  为 Cursor 搭建任务完成通知 hook：macOS/WSL 同时发送系统通知和飞书；
  原生 Linux 只发送飞书。
  支持焦点识别（正盯着 Cursor 窗口时不打扰）、通知 subtitle 显示项目根目录名称、
  使用飞书自建应用发送通知，webhook 仅作可选降级。
  当用户提到"完成后通知我""Cursor 通知 hook""任务做完弹个通知"
  "后台跑任务想被叫一下"、terminal-notifier 或 osascript 通知时，务必使用本 skill。
  即使用户没明说"hook"，只要意图是"让 Cursor 在任务完成时通知我"，也应触发。
---

# Cursor 完成通知 Hook

为 Cursor 配置 `stop` hook，在 Agent 一轮回答结束时发送通知。

### 飞书通知协议

五个 agent 使用同一协议：读取各自 `feishu-agent.env` 中的 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_HOME_CHANNEL`、`FEISHU_APPROVAL_RECEIVE_ID_TYPE`；自建应用 IM API 为主通道，webhook 为失败回退。卡片无按钮，字段固定为 Agent、Project、Content、时间；完成为蓝色，需注意为橙色。通知链路不调用 `lark-cli`。

完成卡片的正文必须使用真实换行来分隔 Agent、Project、Content；不得把 `\n` 作为普通文本发送。时间应作为弱化的说明文字显示。

## 工作原理

```text
Cursor Agent 结束一轮回答
  -> stop hook
  -> notify.sh
  -> macOS 系统通知 / WSL Windows Toast / 飞书
```

Cursor 官方 hooks **不提供** Claude Code 的 `Notification` 或 Codex 的 `PermissionRequest`。不要注册这些事件，也不要用 `preToolUse` 冒充授权提醒——它会在每次工具调用前触发，不是"需要用户处理"。

不要注册 `sessionEnd`：关闭会话会再发一次完成通知。只挂 `stop`。

`stop` 的 stdout 若包含非空 `followup_message`，Cursor 会把它当成自动续跑。通知脚本必须把调试打到 stderr，python/外部命令重定向 stdout，结尾恒 `exit 0`，且 `loop_limit` 设为 `0`。

## 安装步骤

### 0. 先检查环境

```bash
case "$(uname -s)" in
  Darwin) echo 'macOS：系统通知 + 飞书通知' ;;
  Linux)
    if [ -n "${WSL_INTEROP:-}" ] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
      echo 'WSL：Windows 系统通知 + 飞书通知'
    else
      echo 'Linux：仅飞书通知'
    fi
    ;;
  *) echo '不支持本地通知；仅在已配置时发送飞书通知' ;;
esac
```

原生 Linux 仍需安装 stop hook，用它触发飞书；脚本不会尝试 `terminal-notifier`、`osascript` 或终端响铃。

用户级 hook 对 **Cloud Agent 不生效**（云端读不到 `~/.cursor/`）。本 skill 安装用户级配置，覆盖本机 Cursor Desktop / CLI。

### 飞书配置：一键绑定自建应用

通知不调用 `lark-cli`，也不读取其用户登录态。它使用 `feishu-agent.env` 中的应用凭证，先换取 tenant access token，再调用飞书 IM API 向 `FEISHU_HOME_CHANNEL` 发卡片。

每个通知 Skill 都包含完整的 `create_feishu_agent_app.py`，运行期只读取自己的 env，不调用其他 Skill。安装脚本依次使用 Cursor 自己的完整配置、自动复制其他 Agent 的完整配置，或在完全没有配置时扫码创建应用。复用会写入 Cursor 自己的 mode-`600` env，不创建软链接。

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py
python3 -m pip install 'lark-oapi>=1.5.5'
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --home-channel oc_xxx --test
```

`--app-id cli_xxx` 选择指定现有应用；`--new` 跳过自动复用并创建独立应用；`--manual` 打印手工创建步骤。新建应用前必须提供目标会话的 `chat_id`，创建后把机器人加入该会话。`--test` 发送一张连接测试卡片。不要在对话中发送密钥。

### 1. 放置脚本

用户级 hook 的工作目录是 `~/.cursor/`，命令路径写成 `./hooks/notify.sh`：

```bash
mkdir -p ~/.cursor/hooks
cp <skill-dir>/scripts/notify.sh ~/.cursor/hooks/notify.sh
chmod +x ~/.cursor/hooks/notify.sh
```

### 2. 注册 stop hook

**先 Read `~/.cursor/hooks.json`**（可能不存在），合并下面的配置，不要整体覆盖已有 hook：

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "./hooks/notify.sh stop",
        "timeout": 20,
        "loop_limit": 0
      }
    ]
  }
}
```

这是 Cursor 原生格式（`version` + `hooks.<event>[]`），不是 Claude Code 那种两层 `{ hooks: [ { type, command } ] }`。

项目级 hook 放 `.cursor/hooks.json`，命令写成 `.cursor/hooks/notify.sh stop`，且只对该仓库生效。默认安装用户级，让所有项目都能收到完成提醒。

### 3. 校验

```bash
python3 -m json.tool ~/.cursor/hooks.json >/dev/null
jq -e '.hooks.stop[].command' ~/.cursor/hooks.json
bash <skill-dir>/scripts/test_notify.sh
python3 <skill-dir>/scripts/test_feishu_setup.py
```

预期：配置中存在 `stop`，不存在 `sessionEnd`；测试输出 `ok: Cursor stop notification delivered`。

Cursor 会监视 `hooks.json` 并自动重载。若仍未生效，让用户打开 Settings → Hooks，或重启 Cursor。

## 可选：配置飞书 webhook

未配置时自动跳过飞书，不影响本地通知。持久化配置优先使用上方脚本；仅临时测试时可导出：

```bash
export LARK_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export LARK_WEBHOOK_SECRET="..."
```

当自建应用发送失败时，hook 才回退到 webhook。

也可直接编辑 `~/.cursor/feishu-agent.env`：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_HOME_CHANNEL=oc_xxx
# FEISHU_APPROVAL_RECEIVE_ID_TYPE=chat_id
```

`scripts/write_feishu_agent_env.sh` 只作手工兜底。共用应用时，五个 Agent 各自保存一份 env。

## 可选：安装 terminal-notifier

安装第三方软件前先征得用户同意：

```bash
brew install terminal-notifier
```

脚本自动降级：`terminal-notifier` 失败后使用 `osascript`，再失败则使用终端响铃（打到 stderr，避免污染 hook stdout）。

> macOS 12+ 已限制 `-sender`/`-activate` 伪装其他 app，脚本不传这两个参数。Cursor 的 bundle ID 是 `com.todesktop.230313mzl4w4u92`，焦点识别用 `__CFBundleIdentifier` 动态比对，不必写死。

## 脚本设计要求

- 不使用 `set -e/-u/pipefail`；hook 结尾恒定 `exit 0`，不能中断 Cursor。
- stdin 只读一次并复用。
- 焦点识别使用 `lsappinfo`，避免触发自动化权限弹窗。
- `NOTIFY_DEBUG=1` 输出调试信息到 **stderr**；`NOTIFY_FORCE=1` 跳过焦点检查。
- 本地通知 title 为 `Cursor`，subtitle 为项目根目录名称（优先 `cwd`，否则 `workspace_roots[0]`）。
- `status=completed`（或缺省）发蓝色完成卡片，正文 `任务已完成`。
- `status=error` 发橙色需注意卡片，正文 `任务异常结束`。
- `status=aborted` 静默（用户自己点了停止）。
- 不得向 stdout 打印 JSON 或调试文本。

## 验证

```bash
SH=~/.cursor/hooks/notify.sh
bash <skill-dir>/scripts/test_notify.sh
python3 <skill-dir>/scripts/test_feishu_setup.py
echo '{"hook_event_name":"stop","status":"completed","workspace_roots":["/tmp/demo"]}' \
  | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 "$SH" stop
echo '{"status":"aborted"}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 "$SH" stop
```

每条都应 `exit 0`，且 stdout 为空。`NOTIFY_FORCE=1` 用于测试时绕过焦点检查。

## 平台说明

Cursor Desktop / CLI 在 macOS 使用 `terminal-notifier` / `osascript`，WSL 使用 Windows Toast；原生 Linux 不发本地通知，只走飞书。用户正盯着 Cursor 时本地通知和飞书都静默。
