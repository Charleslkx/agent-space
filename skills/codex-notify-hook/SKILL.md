---
name: codex-notify-hook
description: >
  在 macOS 上为 Codex 搭建系统通知 hook：当 Codex 需要用户授权
  （PermissionRequest：shell 提权、apply_patch 编辑等）或任务完成
  （Stop：一轮回答结束）时，自动弹出 macOS 系统通知提醒用户。
  支持焦点识别（正盯着会话窗口时不打扰）、通知 subtitle 显示项目根目录名称、
  可选飞书 webhook 通知、
  以及多依赖缺失时的逐级降级兜底。
  当用户提到"完成后通知我""需要授权时提醒""Codex 通知 hook"
  "任务做完弹个通知""后台跑任务想被叫一下"，或想配置 PermissionRequest/Stop hook、
  terminal-notifier、osascript 通知时，务必使用本 skill。
  即使用户没明说"hook"，只要意图是"让 Codex 在某事件发生时通知我"，也应触发。
---

# Codex macOS 通知 Hook

为 Codex 配置在「需要授权」和「任务完成」时弹出 macOS 系统通知的 hook。核心是两个生命周期事件 + 一个带兜底的投递脚本。

## 工作原理（先理解再动手）

Codex 有两套通知机制：

| 机制 | 触发事件 | 数据传递 | 适用场景 |
|------|---------|---------|---------|
| `notify`（legacy） | 仅 `agent-turn-complete` | JSON 作为 argv 参数 | 只需"任务完成"通知 |
| `[hooks]`（Claude 风格，推荐） | `PermissionRequest`、`Stop`、`PreToolUse` 等 | JSON 通过 stdin | 需要覆盖"需要授权"+"任务完成" |

本 skill 用 `[hooks]` 系统，覆盖两个事件：

| 事件 | 触发时机 | stdin 关键字段 |
|------|---------|--------------|
| `PermissionRequest` | Codex 即将请求授权（shell 提权、apply_patch 编辑、MCP 工具等） | `tool_name`、`tool_input.description`、`tool_input.command` |
| `Stop` | Codex 结束一轮回答（任务完成） | `session_id`、`cwd` 等（无描述文案） |

`PermissionRequest` 对应 Claude Code 的 `Notification`（需要干预）；`Stop` 两边同名同义。命中事件时 Codex 启动 shell 子进程执行配置的命令，把事件 JSON 通过 stdin 喂给该命令。

> **注意**：`PermissionRequest` 只在真正需要审批时触发，不含 Claude Code 的"等待输入空闲催促"。Codex 无此事件，无需过滤。
>
> 按 OpenAI 官方文档，`Approve for me` 的正式配置是交互式 `approval_policy` 配合 `approvals_reviewer = "auto_review"`，它是 reviewer agent 替代人工审批，不是 `permission_mode` 的别名。本 skill 运行时会读取项目级 `.codex/config.toml` 和用户级 `~/.codex/config.toml` 判断是否启用 `auto_review`。
>
> 若已启用 `auto_review`，脚本不再发送"需要授权"提醒；改为在当前 `session_id` 第一次遇到 `PermissionRequest` 时发送一次"已开启 Auto-review，权限申请将由 reviewer 自动处理"。

## 安装步骤

### 1. 放置脚本

把 `scripts/notify.sh` 复制到 Codex 的 hooks 目录并赋可执行权限：

```bash
mkdir -p ~/.codex/hooks
cp <skill-dir>/scripts/notify.sh ~/.codex/hooks/notify.sh
chmod +x ~/.codex/hooks/notify.sh
```

### 2. 注册 hook 到 config.toml

**先 Read `~/.codex/config.toml`，把 hooks 段合并进去，不要整体覆盖**（会冲掉已有的 model/provider/mcp_servers 等）。加入：

```toml
[[hooks.PermissionRequest]]

[[hooks.PermissionRequest.hooks]]
type = "command"
command = "~/.codex/hooks/notify.sh permission-request"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "~/.codex/hooks/notify.sh stop"
```

结构是三层嵌套：事件名 → matcher 组（省略 `matcher` = 匹配所有）→ `{ type = "command", command = "..." }`。`Stop` 的 `matcher` 不支持，省略即可；`PermissionRequest` 的 `matcher` 可按工具名过滤（如 `matcher = "^Bash$"`），省略则所有工具的授权请求都通知。

放在 `~/.codex/config.toml`（用户级）= 所有项目生效；放项目 `.codex/config.toml` = 仅该项目。也可用 `~/.codex/hooks.json`（JSON 格式，结构同官方文档）。

> **与已有 `notify` 共存**：`[hooks]` 与 `notify` 是独立机制，可同时配置互不干扰。若你已有 `notify = ["...", "turn-ended"]`（如 computer-use 客户端），无需改动。

### 3. 信任 hook（重要）

Codex 对非托管（non-managed）hook 要求**审核并信任**后才会运行。配置后启动 Codex，若日志提示 hooks need review：

1. 在 Codex CLI 中运行 `/hooks`
2. 审阅新增的 `PermissionRequest` / `Stop` hook
3. 逐个 trust（信任后按当前 hash 记录，脚本内容变更需重新信任）

信任前 hook 会被静默跳过，通知不会弹出。

### 4. 校验

```bash
# 确认 config.toml 中 hooks 段存在且 TOML 合法
codex debug --config 2>/dev/null | rg -A2 hooks || true
# 或直接检查文件
rg -n "hooks\.(PermissionRequest|Stop)" ~/.codex/config.toml
```

config.toml 一旦 TOML 损坏会**静默禁用该文件所有设置**，务必校验。

### 5. 可选：创建飞书 Agent 应用（Hermes-style）

如果只要发到一个群，incoming webhook 已足够；如果要让机器人发私聊或指定会话通知，需要创建飞书自建应用，而不是自定义机器人 webhook。

本 skill 提供 `scripts/create_feishu_agent_app.py`，复刻 Hermes Agent 的搭建逻辑：

1. 优先用飞书官方 SDK 的一键创建应用能力生成扫码链接。
2. 扫码后飞书创建或更新自建应用，并预填机器人能力、权限、事件和卡片回调。
3. SDK 返回 `App ID` / `App Secret`，用于后续发送机器人消息和 WebSocket 长连接接收 `/set-home`。
4. SDK 不负责配置敏感项（公网回调 URL、加密 key 等）；默认走 WebSocket，避免公网 webhook。

先 dry-run 检查权限清单：

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py
```

真实创建：

```bash
python3 -m pip install 'lark-oapi>=1.5.5'
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live
```

更新已有应用：

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --app-id cli_xxx
```

手工创建兜底：

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py --manual
```

脚本预置的最小权限/事件/回调：

```json
{
  "scopes": {
    "tenant": [
      "im:message.p2p_msg:readonly",
      "im:message.group_at_msg:readonly",
      "im:message:send_as_bot",
      "im:message:update",
      "im:resource",
      "im:chat:read",
      "cardkit:card:read",
      "cardkit:card:write",
      "application:bot.basic_info:read"
    ]
  },
  "events": {
    "items": {
      "tenant": [
        "im.message.receive_v1"
      ]
    }
  },
  "callbacks": {
    "items": [
      "card.action.trigger"
    ]
  }
}
```

扫码创建后建议配置：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=secret_xxx
FEISHU_DOMAIN=feishu
FEISHU_CONNECTION_MODE=websocket
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
```

如果要用 `/set-home` 自动记录接收会话，需要一个网关进程维持 WebSocket 长连接；飞书审批卡片的按钮回调同样走这个网关。

### 6. 可选：启用飞书 Agent 通知链路

本 skill 提供飞书 Agent 审批卡片链路：

```text
PermissionRequest hook
  -> feishu_send_approval.py 发交互卡片
  -> 飞书显示项目、内容、Allow once / Deny 按钮
  -> 用户点击后由 feishu_approval_gateway.py 接收 card.action.trigger
  -> 网关记录结果并把原卡片更新为已批准 / 已拒绝
```

安装到当前 Codex：

```bash
cp <skill-dir>/scripts/notify.sh ~/.codex/hooks/notify.sh
cp <skill-dir>/scripts/feishu_approval_common.py ~/.codex/hooks/
cp <skill-dir>/scripts/feishu_send_approval.py ~/.codex/hooks/
cp <skill-dir>/scripts/feishu_approval_gateway.py ~/.codex/hooks/
chmod +x ~/.codex/hooks/notify.sh ~/.codex/hooks/feishu_send_approval.py ~/.codex/hooks/feishu_approval_gateway.py
```

网关需要 `~/.codex/feishu-agent.env`：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=secret_xxx
FEISHU_DOMAIN=feishu
FEISHU_CONNECTION_MODE=websocket
FEISHU_APPROVAL_RECEIVE_ID_TYPE=chat_id
```

启动网关：

```bash
/Users/charles/Nutstore/agent-space/.venv/bin/python ~/.codex/hooks/feishu_approval_gateway.py
```

设置通知接收会话：

```text
/set-home
```

把这条消息发给飞书机器人；网关会把当前 `chat_id` 写入 `FEISHU_HOME_CHANNEL`，之后飞书通知会发到该会话。

macOS 常驻可用 LaunchAgent：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.charles.codex-feishu-approval.plist
launchctl kickstart -k gui/$(id -u)/com.charles.codex-feishu-approval
```

日志：

```bash
tail -f ~/.codex/log/feishu-approval-gateway.out.log ~/.codex/log/feishu-approval-gateway.err.log
```

当前行为：发送飞书交互卡片，记录按钮选择并更新卡片状态；是否真正替代 Codex 原生审批提示，取决于上层审批编排是否消费这些结果。

### 7. 可选：配置飞书 webhook

脚本会读取两个环境变量；未配置时自动跳过飞书，不影响本地通知：

```bash
export LARK_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export LARK_WEBHOOK_SECRET="..." # 机器人未启用签名校验时可不填
```

飞书消息固定为三行：`Agent: Codex`、`Project: <项目根目录名称>`、`Content: <通知内容>`。

incoming webhook 仍然只承载简单通知；带按钮的审批交互只走 Feishu Agent。

### 8. 可选：装 terminal-notifier 以获得更好的通知体验

原生 `osascript` 通知功能有限。`terminal-notifier` 投递更稳定（安装第三方软件，先征得用户同意）：

```bash
brew install terminal-notifier
```

脚本自动检测：装了就用它，没装回退原生通知。

> **注意**：macOS 12+ 已限制 `-sender`/`-activate` 伪装其他 app，会导致通知被静默丢弃，脚本不传这两个参数。通知图标为 terminal-notifier 自身图标，subtitle 显示项目根目录名称。

### 9. 生效说明

config 改动通常下一轮即生效。若 hook 需要信任，先用 `/hooks` 审核信任，或重启 Codex。

## 脚本设计要点（`notify.sh`）

脚本接收一个参数区分事件（`permission-request` / `stop`），从 stdin 读事件 JSON，全部逻辑集中于此。四条独立**降级链**，任一环失效自动落到下一级，且脚本**恒定 `exit 0`、绝不阻塞** Codex：

| 环节 | 一级 | 二级 | 三级 |
|------|------|------|------|
| 事件识别 | 位置参数 `$1` | stdin JSON 的 `hook_event_name`（jq） | 纯文本 grep/sed |
| 消息解析 | `jq` 取 `tool_name`/`tool_input` | 纯文本抓取 | 默认文案 |
| 项目识别 | `NOTIFY_PROJECT_DIR` | stdin JSON 的 `cwd` | 当前工作目录 |
| 焦点识别 | `lsappinfo` + `__CFBundleIdentifier` 比对 | 任一信号缺失 → 跳过（宁可多弹） | — |
| 本地通知投递 | `terminal-notifier`（subtitle 显示项目名） | `osascript` 原生 | 终端响铃 `\a` |
| 飞书通知投递 | `python3` 标准库 webhook | 未配置则跳过 | — |

关键设计原则（改脚本时务必保持）：

- **不用 `set -e/-u/pipefail`**：通知 hook 首要目标是"绝不因自身报错而中断会话"。所有外部命令前用 `command -v` 探测、用 `|| true` 兜底、结尾恒 `exit 0`。
- **stdin 只读一次**存入 `INPUT` 变量，事件识别与消息解析复用，避免二次读取读空。
- **Auto-review 检测走 config，不猜 payload**：用 `python3` 标准库 `tomllib` 读取项目级/用户级 `config.toml`，判断 `approvals_reviewer = "auto_review"`；不把 `permission_mode` 当成 reviewer 状态。
- **同步试投检测崩溃**：`terminal-notifier` 在 macOS 26 (Tahoe) 上已不维护会 `Abort trap: 6` 崩溃，必须同步试投（非后台）才能检测到非零退出并降级到 osascript。正常系统上约 2s 返回可接受；崩溃系统上瞬时失败立即降级。
- **焦点识别用 `lsappinfo` 不用 System Events**：前者查 CoreServices DB 无需自动化授权弹窗，后者会弹权限框。
- **焦点是应用级非窗口级**：在 VSCode 但看的是代码而非集成终端、或开了多个同 app 窗口时，会误判为"在焦点"而静默。需窗口级要用 Accessibility 脚本逐 app 取窗口标题，代价大且对 VSCode 集成终端不可靠，一般不做。
- **远程/SSH 会话**：`__CFBundleIdentifier` 为空时自动跳过焦点检查，通知照常弹出。
- **授权消息构造**：优先 `tool_input.description`（人类可读理由），其次 `tool_input.command`（截断 120 字符），再次 `tool_name`，最后默认文案。
- **Auto-review 只提示一次**：按 `session_id` 在临时目录打一个轻量标记，避免每次边界请求都重复提示"已开启 Auto-review"。
- **通知内容保持简单**：本地通知 title 是 `Codex`，subtitle 是项目根目录名称，body 是通知内容；飞书同样只发 agent、project、content。

## 自定义

- **改提示音**：脚本顶部 `SOUND="Glass"`，可换 `/System/Library/Sounds/` 下任意名（Ping/Hero/Submarine...）。
- **改标题/文案**：顶部 `TITLE="Codex"`、`DEFAULT_MSG`，及 `case` 分支里的文案。
- **调试**：`NOTIFY_DEBUG=1` 打印决策到 stderr；`NOTIFY_FORCE=1` 跳过焦点检查强制弹出。

## 验证（沿用这些命令自测）

```bash
SH=~/.codex/hooks/notify.sh
# 飞书 Agent 应用创建 dry-run → 应打印权限/事件/回调 JSON
python3 <skill-dir>/scripts/create_feishu_agent_app.py
# 授权类（带描述）→ 应弹出"需要授权: Bash — Run brew install node"
echo '{"hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"description":"Run brew install node","command":"brew install node"}}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH permission-request
# Auto-review 已开启 → 不弹"需要授权"，仅首次提示"已开启 Auto-review，权限申请将由 reviewer 自动处理"
mkdir -p /tmp/codex-notify-test/.codex
cat >/tmp/codex-notify-test/.codex/config.toml <<'EOF'
approvals_reviewer = "auto_review"
EOF
echo '{"hook_event_name":"PermissionRequest","session_id":"demo-session","cwd":"/tmp/codex-notify-test","tool_name":"Bash","tool_input":{"command":"git push origin main"}}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH permission-request
# 授权类（仅命令）→ 应弹出"需要授权: Bash — git push origin main"
echo '{"hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"git push origin main"}}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH permission-request
# 任务完成 → 应弹出"任务已完成"
echo '{"hook_event_name":"Stop"}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH stop
# 飞书格式自测（用无效 URL 也应快速失败且 exit 0）
echo '{"hook_event_name":"Stop","cwd":"/tmp/demo-project"}' | LARK_WEBHOOK_URL=http://127.0.0.1:9 NOTIFY_FORCE=1 $SH stop
# 自动识别事件（无位置参数，从 stdin hook_event_name 推断）→ 应弹出
echo '{"hook_event_name":"PermissionRequest","tool_name":"apply_patch","tool_input":{"description":"Edit file.py"}}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH
# 无 terminal-notifier → 退 osascript
echo '{"hook_event_name":"Stop"}' | NOTIFY_FORCE=1 PATH=/usr/bin:/bin $SH stop
# 极端 PATH（连 grep/osascript 都没有）→ 响铃兜底，仍 exit 0
echo '{"hook_event_name":"Stop"}' | NOTIFY_FORCE=1 PATH=/bin /bin/bash $SH stop
```

每条都应 `exit 0`。`NOTIFY_FORCE=1` 用于测试时绕过焦点检查（否则你正盯着终端会被静默）。

## 平台说明

仅 macOS（依赖 `osascript`/`lsappinfo`，`__CFBundleIdentifier` 由 macOS GUI 启动注入）。Linux 需改用 `notify-send`、Windows 需改用 PowerShell `BurntToast`——投递层 `deliver()` 是唯一需要替换的部分，事件结构与降级思路通用。

## 与 Claude Code notify hook 的差异

| 维度 | Claude Code | Codex（本 skill） |
|------|------------|-------------------|
| 配置文件 | `~/.claude/settings.json`（JSON） | `~/.codex/config.toml`（TOML） |
| 事件注册 | `hooks.Notification` / `hooks.Stop` | `[[hooks.PermissionRequest]]` / `[[hooks.Stop]]` |
| 需要授权事件 | `Notification`（含授权+空闲催促） | `PermissionRequest`（仅授权，无空闲催促） |
| stdin 消息字段 | `.message`（现成文案） | `tool_name`+`tool_input`（脚本自行构造文案） |
| 信任机制 | 无 | `/hooks` 审核信任（hash 变更需重信） |
| 与 legacy notify | — | `[hooks]` 与 `notify` 独立共存 |
