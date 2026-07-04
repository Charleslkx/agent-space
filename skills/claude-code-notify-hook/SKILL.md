---
name: claude-code-notify-hook
description: >
  在 macOS 上为 Claude Code 搭建系统通知 hook：当 Claude Code 需要用户干预
  （请求工具授权、等待输入）或任务完成时，自动弹出 macOS 系统通知提醒用户。
  支持焦点识别（正盯着会话窗口时不打扰）、通知 subtitle 显示项目根目录名称、
  可选飞书 Agent / webhook 通知、
  以及多依赖缺失时的逐级降级兜底。
  当用户提到"完成后通知我""需要授权时提醒""Claude Code 通知 hook"
  "任务做完弹个通知""后台跑任务想被叫一下"，或想配置 Notification/Stop hook、
  terminal-notifier、osascript 通知时，务必使用本 skill。
  即使用户没明说"hook"，只要意图是"让 Claude Code 在某事件发生时通知我"，也应触发。
---

# Claude Code macOS 通知 Hook

为 Claude Code 配置在「需要干预」和「任务完成」时弹出 macOS 系统通知的 hook。核心是两个生命周期事件 + 一个带兜底的投递脚本。

## 工作原理（先理解再动手）

Claude Code 在生命周期里埋了事件点。命中事件时，它**启动一个 shell 子进程**执行你配置的命令，并把一段 **JSON 通过 stdin** 喂给该命令。所以"事件发生时自动通知"只能用 hook 实现，记忆/偏好做不到——是 Claude Code 程序在跑命令，不是模型主动发。

本 skill 用到两个事件：

| 事件 | 触发时机 | stdin 是否带 `message` |
|------|---------|----------------------|
| `Notification` | 需要工具授权 / 长时间等待用户输入 | 有（如 `Claude needs your permission to use Bash`） |
| `Stop` | Claude 结束一轮回答（任务完成） | 无（带 session_id 等，无描述文案） |

`Notification` 是"需要注意"的总入口，**不止授权**——空闲等待也会触发，文案是 `...waiting for your input`。脚本会过滤掉这条（与 Stop 完成通知重复），避免每次任务后被重复打扰。

## 安装步骤

### 1. 放置脚本

把 `scripts/notify.sh` 复制到用户的 hooks 目录并赋可执行权限：

```bash
mkdir -p ~/.claude/hooks
cp <skill-dir>/scripts/notify.sh ~/.claude/hooks/notify.sh
chmod +x ~/.claude/hooks/notify.sh
```

### 2. 注册 hook 到 settings.json

**先 Read `~/.claude/settings.json`，把 hooks 合并进去，不要整体覆盖**（会冲掉用户已有的 model/theme/permissions 等）。加入：

```json
{
  "hooks": {
    "Notification": [
      { "hooks": [ { "type": "command", "command": "~/.claude/hooks/notify.sh notification" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "~/.claude/hooks/notify.sh stop" } ] }
    ]
  }
}
```

注意 hooks 是**两层嵌套**：事件名 → 数组 → `{ hooks: [ { type, command } ] }`。外层元素可带 `matcher` 按工具名过滤；通知类无需过滤，省略即可。

放在 `~/.claude/settings.json`（用户级）= 所有项目生效；放项目 `.claude/settings.json` = 仅该项目。

### 3. 校验

```bash
jq -e '.hooks.Notification[].hooks[].command, .hooks.Stop[].hooks[].command' ~/.claude/settings.json
```

退出 0 且打印出两条命令 = 配置正确。settings.json 一旦 JSON 损坏会**静默禁用该文件所有设置**，务必校验。

### 4. 可选：配置飞书 Agent 通知

若已经按 `codex-notify-hook` 创建 Feishu Agent 应用，可直接复用同一套脚本和网关：

```bash
cp <codex-notify-hook-dir>/scripts/feishu_approval_common.py ~/.codex/hooks/
cp <codex-notify-hook-dir>/scripts/feishu_send_approval.py ~/.codex/hooks/
cp <codex-notify-hook-dir>/scripts/feishu_approval_gateway.py ~/.codex/hooks/
```

`~/.codex/feishu-agent.env` 至少需要：

```bash
FEISHU_DOMAIN=feishu
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_HOME_CHANNEL=oc_xxx
```

`Notification` 事件命中授权文案时，脚本会先弹本地通知，再发送飞书文本消息。消息内容保持三项：`Agent: ClaudeCode`、`Project: <项目根目录名称>`、`Content: <通知内容>`。

`FEISHU_HOME_CHANNEL` 可由网关收到机器人私聊 `/set-home` 后写入。网关建议用 LaunchAgent 常驻，具体创建步骤沿用 `codex-notify-hook`。

当前行为：只发送飞书文本通知，不记录选择、不等待结果、不替代 Claude Code 原生审批提示。

### 5. 可选：配置飞书 webhook 兜底

脚本会读取两个环境变量；未配置时自动跳过 webhook，不影响本地通知或 Agent 通知：

```bash
export LARK_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export LARK_WEBHOOK_SECRET="..." # 机器人未启用签名校验时可不填
```

飞书消息固定为三行：`Agent: ClaudeCode`、`Project: <项目根目录名称>`、`Content: <通知内容>`。

incoming webhook 和 Feishu Agent 当前都只承载通知；不要在消息里添加操作按钮。

### 6. 可选：装 terminal-notifier 以获得更好的通知体验

原生 `osascript` 通知功能有限。`terminal-notifier` 投递更稳定（安装第三方软件，先征得用户同意）：

```bash
brew install terminal-notifier
```

脚本会自动检测：装了就用它，没装就回退原生通知。

> **注意**：macOS 12+ 已限制 `-sender`/`-activate` 伪装其他 app，会导致通知被静默丢弃，脚本不传这两个参数。通知图标为 terminal-notifier 自身图标，subtitle 显示项目根目录名称。

### 7. 生效说明

settings 改动通常下一轮即生效。若没生效，让用户打开一次 `/hooks` 重新加载，或重启 Claude Code（你无法代替用户开 `/hooks` 菜单）。

## 脚本设计要点（`notify.sh`）

脚本接收一个参数区分事件（`notification` / `stop`），从 stdin 读事件 JSON，全部逻辑集中于此。四条独立**降级链**，任一环失效自动落到下一级，且脚本**恒定 `exit 0`、绝不阻塞** Claude Code：

| 环节 | 一级 | 二级 | 三级 |
|------|------|------|------|
| 消息解析 | `jq` 取 `.message` | 纯文本 `grep`/`sed` 抓取 | 默认文案 |
| 项目识别 | `NOTIFY_PROJECT_DIR` | stdin JSON 的 `cwd` | 当前工作目录 |
| 焦点识别 | `lsappinfo` + `__CFBundleIdentifier` 比对 | 任一信号缺失 → 跳过（宁可多弹） | — |
| 本地通知投递 | `terminal-notifier`（subtitle 显示项目名） | `osascript` 原生 | 终端响铃 `\a` |
| 飞书投递 | Feishu Agent 通知 | webhook 兜底 | 未配置则跳过 |

关键设计原则（改脚本时务必保持）：

- **不用 `set -e/-u/pipefail`**：通知 hook 首要目标是"绝不因自身报错而中断会话"。所有外部命令前用 `command -v` 探测、用 `|| true` 兜底、结尾恒 `exit 0`。
- **stdin 只读一次**存入 `INPUT` 变量，消息解析与空闲过滤复用，避免二次读取读空。
- **后台投递 + disown**：`terminal-notifier` 约 2s 才返回，必须 `( ... ) & disown` 后台化，否则阻塞 hook（实测后台化后 hook ~0.006s 返回）。
- **焦点识别用 `lsappinfo` 不用 System Events**：前者查 CoreServices DB 无需自动化授权弹窗，后者会弹权限框。
- **焦点是应用级非窗口级**：在 VSCode 但看的是代码而非集成终端、或开了多个同 app 窗口时，会误判为"在焦点"而静默。需窗口级要用 Accessibility 脚本逐 app 取窗口标题，代价大且对 VSCode 集成终端不可靠，一般不做。
- **远程/SSH 会话**：`__CFBundleIdentifier` 为空时自动跳过焦点检查，通知照常弹出。
- **通知内容保持简单**：本地通知 title 是 `ClaudeCode`，subtitle 是项目根目录名称，body 是通知内容；飞书同样只发 agent、project、content。

## 自定义

- **改提示音**：脚本顶部 `SOUND="Glass"`，可换 `/System/Library/Sounds/` 下任意名（Ping/Hero/Submarine...）。
- **改标题/文案**：顶部 `TITLE`、`DEFAULT_MSG`，及 `case` 分支里的文案。
- **调试**：`NOTIFY_DEBUG=1` 打印决策到 stderr；`NOTIFY_FORCE=1` 跳过焦点检查强制弹出。

## 验证（沿用这些命令自测）

```bash
SH=~/.claude/hooks/notify.sh
# 授权类 → 应弹出
echo '{"message":"Claude needs your permission to use Bash"}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH notification
# 空闲催促 → 应静默
echo '{"message":"Claude is waiting for your input"}' | NOTIFY_DEBUG=1 $SH notification
# 任务完成 → 应弹出
echo '{}' | NOTIFY_DEBUG=1 NOTIFY_FORCE=1 $SH stop
# 飞书格式自测（用无效 URL 也应快速失败且 exit 0）
echo '{"message":"x","cwd":"/tmp/demo-project"}' | LARK_WEBHOOK_URL=http://127.0.0.1:9 NOTIFY_FORCE=1 $SH notification
# 无 terminal-notifier → 退 osascript
echo '{"message":"x"}' | NOTIFY_FORCE=1 PATH=/usr/bin:/bin $SH notification
# 极端 PATH（连 grep/osascript 都没有）→ 响铃兜底，仍 exit 0
echo '{"message":"x"}' | NOTIFY_FORCE=1 PATH=/bin /bin/bash $SH notification
```

每条都应 `exit 0`。`NOTIFY_FORCE=1` 用于测试时绕过焦点检查（否则你正盯着终端会被静默）。

## 平台说明

仅 macOS（依赖 `osascript`/`lsappinfo`，`__CFBundleIdentifier` 由 macOS GUI 启动注入）。Linux 需改用 `notify-send`、Windows 需改用 PowerShell `BurntToast`——投递层 `deliver()` 是唯一需要替换的部分，事件结构与降级思路通用。
