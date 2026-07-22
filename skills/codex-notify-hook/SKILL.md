---
name: codex-notify-hook
description: >
  为 Codex 搭建任务完成通知 hook：macOS/WSL 同时发送系统通知和飞书；
  原生 Linux 只发送飞书。
  支持焦点识别（正盯着会话窗口时不打扰）、通知 subtitle 显示项目根目录名称、
  使用飞书自建应用发送通知，webhook 仅作可选降级。
  当用户提到"完成后通知我""Codex 通知 hook""任务做完弹个通知"
  "后台跑任务想被叫一下"、terminal-notifier 或 osascript 通知时，务必使用本 skill。
---

# Codex 完成通知 Hook

为 Codex 配置 `Stop` hook，在一轮回答结束时发送通知。

### 飞书通知协议

三个 agent 使用同一协议：读取各自 `feishu-agent.env` 中的 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_HOME_CHANNEL`、`FEISHU_APPROVAL_RECEIVE_ID_TYPE`；自建应用 IM API 为主通道，webhook 为失败回退。卡片无按钮，字段固定为 Agent、Project、Content、时间；完成为蓝色，需注意为橙色。通知链路不调用 `lark-cli`。

完成卡片的正文必须使用真实换行来分隔 Agent、Project、Content；不得把 `\n` 作为普通文本发送。时间应作为弱化的说明文字显示。

## 工作原理

```text
Codex CLI 结束一轮回答
  -> Stop hook
  -> notify.sh
  -> macOS 系统通知 / WSL Windows Toast / 飞书
```

本 skill 不注册 `PermissionRequest`。该事件发生在自动审批之前，载荷没有自动审批结果，无法区分自动通过和需要人工授权；注册它会产生错误提醒。只有 Codex 提供后置人工授权事件或 `requires_user_input` 字段后，才重新启用。

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

原生 Linux 仍需安装 Stop hook，用它触发飞书；脚本不会尝试 `terminal-notifier`、`osascript` 或终端响铃。Codex App 会通过 `CODEX_INTERNAL_ORIGINATOR_OVERRIDE=Codex Desktop` 和 `__CFBundleIdentifier=com.openai.codex` 被识别，跳过本地通知，只保留飞书；App 自身负责系统提醒。

### 飞书配置：一键绑定自建应用

通知不调用 `lark-cli`，也不读取其用户登录态。它使用 `feishu-agent.env` 中的应用凭证，先换取 tenant access token，再调用飞书 IM API 向 `FEISHU_HOME_CHANNEL` 发卡片。

已有绑定应用时，保留现有 `~/.codex/feishu-agent.env`。需要新建或更新应用时，用户在本机运行：

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live
```

该命令通过 `lark_oapi.register_app` 展示二维码，完成飞书一键绑定；将输出的凭证和接收群 `FEISHU_HOME_CHANNEL` 写入 `~/.codex/feishu-agent.env`（权限 `600`）。不要在对话中发送密钥。

脚本与三个 Python 文件必须一并放入 hook 目录：

```bash
mkdir -p ~/.codex/hooks
cp <skill-dir>/scripts/{notify.sh,feishu_send_approval.py,feishu_approval_common.py} ~/.codex/hooks/
chmod +x ~/.codex/hooks/notify.sh
```

完成事件会发蓝色完成卡片；授权事件才发带按钮的审批卡片。

### 1. 放置脚本

```bash
mkdir -p ~/.codex/hooks
cp <skill-dir>/scripts/{notify.sh,feishu_send_approval.py,feishu_approval_common.py} ~/.codex/hooks/
chmod +x ~/.codex/hooks/notify.sh
```

### 2. 注册 Stop hook

先读取 `~/.codex/config.toml`，合并下面的配置，不要整体覆盖：

```toml
[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "~/.codex/hooks/notify.sh stop"
```

如果使用 `~/.codex/hooks.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.codex/hooks/notify.sh stop"
          }
        ]
      }
    ]
  }
}
```

删除已有的 `PermissionRequest` 注册，不要把 `notify.sh permission-request` 挂载到任何 hook。

### 3. 信任 hook

Codex 对非托管 hook 要求审核并信任：

1. 在 Codex CLI 中运行 `/hooks`。
2. 审阅新增的 `Stop` hook。
3. trust 当前脚本；脚本内容变化后需要重新信任。

### 4. 校验

```bash
rg -n '"Stop"|PermissionRequest' ~/.codex/hooks.json
bash <skill-dir>/scripts/test_notify.sh
```

预期：配置中存在 `Stop`，不存在 `PermissionRequest`；测试输出 `ok: Stop notification delivered`。

## 可选：配置飞书 webhook

未配置时自动跳过飞书，不影响本地通知。持久化配置优先使用上方脚本；仅临时测试时可导出：

```bash
export LARK_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export LARK_WEBHOOK_SECRET="..."
```

当自建应用发送失败时，hook 才回退到 webhook。

## 可选：安装 terminal-notifier

安装第三方软件前先征得用户同意：

```bash
brew install terminal-notifier
```

脚本自动降级：`terminal-notifier` 失败后使用 `osascript`，再失败则使用终端响铃。

## 脚本设计要求

- 不使用 `set -e/-u/pipefail`；hook 结尾恒定 `exit 0`，不能中断 Codex。
- stdin 只读一次并复用。
- 焦点识别使用 `lsappinfo`，避免触发自动化权限弹窗。
- `NOTIFY_DEBUG=1` 输出调试信息；`NOTIFY_FORCE=1` 跳过焦点检查。
- 本地通知 title 为 `Codex`，subtitle 为项目根目录名称，body 为 `任务已完成`。

## 验证

```bash
SH=~/.codex/hooks/notify.sh
bash <skill-dir>/scripts/test_notify.sh
echo '{"hook_event_name":"Stop"}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 "$SH" stop
echo '{"hook_event_name":"Stop"}' | NOTIFY_FORCE=1 PATH=/usr/bin:/bin "$SH" stop
```

每条都应 `exit 0`。`NOTIFY_FORCE=1` 用于测试时绕过焦点检查。

## 平台说明

Codex CLI 在 macOS 使用 `terminal-notifier` / `osascript`，WSL 使用 Windows Toast；Codex App 与原生 Linux 不发本地通知，只走飞书。
