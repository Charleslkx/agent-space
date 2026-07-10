---
name: opencode-notify-hook
description: >
  在 macOS 上为 opencode 搭建系统通知插件：当 opencode 需要用户授权
  （permission.asked：bash 提权、文件编辑等）或任务完成（session.idle）时，
  自动弹出 macOS 系统通知提醒用户。
  支持焦点识别（正盯着会话窗口时不打扰）、项目名 subtitle、可选飞书 Agent / webhook、
  以及多依赖缺失时的逐级降级兜底。
  当用户提到"完成后通知我""需要授权时提醒""opencode 通知""任务做完弹个通知"
  "后台跑任务想被叫一下"，或想配置 opencode 插件通知时，务必使用本 skill。
  即使用户没明说"插件"，只要意图是"让 opencode 在某事件发生时通知我"，也应触发。
---

# opencode macOS 通知插件

为 opencode 配置在「需要授权」和「任务完成」时弹出 macOS 系统通知的插件。
核心是一个 JS 插件文件，监听两个生命周期事件。

## 与 Claude Code / Codex hook 的对比

| 维度 | Claude Code | Codex | opencode（本 skill） |
|------|------------|-------|---------------------|
| 配置文件 | `~/.claude/settings.json`（JSON） | `~/.codex/config.toml`（TOML） | 无需配置，插件自动加载 |
| 机制 | shell 命令 hook | shell 命令 hook | JS 插件（`~/.config/opencode/plugins/`） |
| 需授权事件 | `Notification` | `PermissionRequest` | `permission.asked` |
| 完成事件 | `Stop` | `Stop` | `session.idle` |
| 信任机制 | 无 | `/hooks` 审核 | 无（本地文件自动信任） |
| 脚本语言 | bash | bash | JavaScript（ESM） |

## 工作原理

opencode 用 **JS 插件系统**而非 shell hook。插件放在
`~/.config/opencode/plugins/` 目录后自动加载，无需在 `opencode.json` 注册。

插件通过 `event` 钩子订阅所有总线事件，命中关心的事件类型时触发通知。

| 事件 | 触发时机 | 对应行为 |
|------|---------|---------|
| `permission.asked` | opencode 即将申请权限（bash 执行、文件编辑等） | 弹授权通知 |
| `session.idle` | 一轮回答结束，任务完成 | 弹完成通知 |

## 安装步骤

### 1. 放置插件

```bash
mkdir -p ~/.config/opencode/plugins
cp <skill-dir>/plugins/notify.js ~/.config/opencode/plugins/notify.js
```

无需额外配置，opencode 启动时自动扫描该目录并加载所有 `.js`/`.ts` 文件。

### 2. 可选：配置飞书 Agent 通知

**与 codex 完全解耦，插件自带 IM API 发送（纯 Node 标准库），无需 codex 脚本或网关。** 凭证放在 `~/.config/opencode/feishu-agent.env`。

#### 2a. 一键创建 / 选择应用（推荐）

`scripts/create_feishu_agent_app.py` 复刻 codex 的一键创建流程：扫码后飞书创建或**选择已有应用**，SDK 返回 App ID / App Secret，脚本直接写入 `~/.config/opencode/feishu-agent.env`（mode 600），无需手工复制或软链。

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py            # dry-run，打印将提交的权限清单
python3 -m pip install 'lark-oapi>=1.5.5'
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live     # 扫码，新建一个应用
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --app-id cli_xxx  # 共用同一应用：指定已有 app_id
python3 <skill-dir>/scripts/create_feishu_agent_app.py --manual   # 手工创建兜底
```

env 路径可用 `--env-out` 改写；`--env-out ''` 只打印不落盘。

> **共用一个应用的注意点**：三个 agent 各自跑本脚本、各写各的 env，指向同一 app_id 时拿到的是**同一个 App Secret**（飞书一个应用只有一个 secret）。若某次重新授权导致飞书**重置了 secret**，其余 agent 的旧 secret 会失效——此时重跑另外两个 agent 的创建脚本刷新即可。

#### 2b. 手工填写

也可直接编辑 `~/.config/opencode/feishu-agent.env`：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_HOME_CHANNEL=oc_xxx           # 或 FEISHU_APPROVAL_RECEIVE_ID
# FEISHU_APPROVAL_RECEIVE_ID_TYPE=chat_id   # 默认 chat_id
```

插件读取该文件、用 `app_id/app_secret` 取 `tenant_access_token`，再经 `im/v1/messages` 直接投递飞书**交互卡片**（`msg_type=interactive`）。env 缺失或凭证不全时自动跳过，不影响本地通知。env 路径可用 `FEISHU_ENV` 覆盖。

`permission.asked` 与 `session.idle` 都会发卡片：完成（`session.idle`）绿色标题 `🤖 OpenCode · 任务完成`，需要授权（`permission.asked`）橙色标题 `⚠️ OpenCode · 需要授权`；正文含 `Agent` / `Project` 两个字段、通知内容和时间戳 note。

当前行为：只发送飞书通知卡片（**无操作按钮**），不记录选择、不等待结果、不替代 OpenCode 原生审批提示。

### 3. 可选：配置飞书 webhook 兜底

插件会读取两个环境变量；未配置时自动跳过 webhook，不影响本地通知或 Agent 通知：

```bash
export LARK_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export LARK_WEBHOOK_SECRET="..." # 机器人未启用签名校验时可不填
```

webhook 兜底发送与 IM API 同款**交互卡片**（绿=完成/橙=授权，含 Agent/Project/内容/时间戳），仅在 IM API 发送失败时使用。

incoming webhook 和 Feishu Agent 当前都只承载通知；不要在卡片里添加操作按钮（自定义 bot webhook 收不到按钮回调）。

### 4. 可选：安装 terminal-notifier

原生 `osascript` 通知功能有限。`terminal-notifier` 投递更稳定（需用户同意安装）：

```bash
brew install terminal-notifier
```

插件自动检测：装了就用它，没装回退 `osascript`，两者都没有则终端响铃。

> **注意**：macOS 26 (Tahoe) 上 terminal-notifier 可能崩溃（`Abort trap: 6`）。
> 插件用**同步试投**检测崩溃后自动降级到 osascript，无需手动处理。

### 5. 校验

重启 opencode 后，触发任意权限请求或等待任务完成，应看到系统通知弹出。

调试：直接在 node 里加载插件检查结构（无需 `$` helper，插件内部用 `child_process`）：

```bash
node --input-type=module <<'EOF'
import { OpenCodeNotifyPlugin } from "/Users/charles/.config/opencode/plugins/notify.js"
const plugin = await OpenCodeNotifyPlugin()
console.log("plugin hooks:", Object.keys(plugin))
EOF
```

## 插件设计要点

- **ESM 格式**：使用 `export const` 具名导出，opencode 插件系统要求 ESM。
- **同步试投检测崩溃**：terminal-notifier 在 macOS 26 上崩溃会立即返回非零，同步等待才能检测到并降级；异步 fire-and-forget 会漏掉崩溃。
- **焦点识别**：见下方「ASN 问题与焦点检测」一节。
- **`event.properties` 防御**：不同 opencode 版本的事件 payload 字段名可能有差异，`extractPermissionMsg` 多重 fallback 兜底，最终退到"需要授权"默认文案。
- **通知内容保持简单**：本地通知 title 是 `OpenCode`，subtitle 是项目根目录名称，body 是通知内容；飞书发通知卡片（绿=完成/橙=授权），卡片只含 agent、project、content、时间戳，不加按钮。
- **不 `throw`**：插件内所有路径 `.catch(() => {})` 兜底，确保通知失败不中断 opencode 会话。

## ASN 问题与焦点检测

焦点检测决定「用户正盯着会话窗口时是否静默」。实现上有个 macOS `lsappinfo` 的坑必须避开。

### 问题

`lsappinfo front` **不返回 bundle ID**，只返回一个 ASN 标识符：

```
$ lsappinfo front
ASN:0x0-0x1829828:
```

旧版代码用 `/__CFBundleIdentifier="([^"]+)"/` 正则直接匹配 `lsappinfo front` 的输出，**永远匹配不上** —— `bundleId` 恒为空，`isFocused()` 恒返回 `false`，焦点检测完全失效（无论用户是否在看终端，都会弹通知）。

### 修复：两步 lsappinfo

正确做法是分两步：先拿 ASN，再用 ASN 查 bundle ID。

```js
// 1. 拿前台 ASN
const { stdout: asn } = await run("lsappinfo", ["front"])   // "ASN:0x0-0x1829828:"

// 2. 用 ASN 查 bundle ID
const { stdout: info } = await run("lsappinfo", ["info", "-only", "bundleid", asn.trim()])
// 输出: "CFBundleIdentifier"="com.exafunction.windsurf"
const front = info.match(/"CFBundleIdentifier"="([^"]+)"/)?.[1] ?? ""
```

### owner vs front 比较

光知道前台 app 的 bundle ID 还不够 —— 旧代码只判断「前台是不是任意终端」
（`TERMINAL_BUNDLES.some(b => bundleId.startsWith(b))`），会导致 opencode 跑在
Terminal 里、用户切到 iTerm2 时也被误判为「聚焦」而静默。

修复后引入 `owner = process.env.__CFBundleIdentifier`（macOS 注入的、承载 opencode
的那个终端的 bundle ID），只有 `front === owner` 时才静默：

```js
async function isFocused() {
  const owner = process.env.__CFBundleIdentifier
  if (!owner) return false          // SSH/远程拿不到 owner → 永远通知
  const front = await frontBundleId()
  if (!front) return false
  return TERMINAL_BUNDLES.includes(owner) && front === owner
}
```

### 为什么不用 opencode 的 `$` helper

插件内所有 shell 调用都走 `node:child_process`（`stdio: ignore/pipe`），而不是 opencode
的 `$` 模板 helper。原因：

1. `$` 会把命令输出回显到 opencode TUI —— `lsappinfo` 的 ASN 输出会变成「顶层消息」
   遮盖终端界面。
2. 两步 `lsappinfo` 需要把第一步的 ASN 传给第二步，用 `child_process` 的 `run()` 封装
   更直观。

`child_process` 对 opencode TUI 完全不可见，不会污染界面。

### 调试

```bash
# 确认 lsappinfo 输出格式
lsappinfo front                      # 应输出 ASN:0x...
lsappinfo info -only bundleid $(lsappinfo front)   # 应输出 "CFBundleIdentifier"="..."

# 确认 owner
echo $__CFBundleIdentifier            # 承载 opencode 的终端 bundle ID

# 在 node 里直接测插件
node --input-type=module <<'EOF'
import { OpenCodeNotifyPlugin } from "./plugins/notify.js"
const plugin = await OpenCodeNotifyPlugin()
console.log("plugin hooks:", Object.keys(plugin))
EOF
```


- **改提示音**：修改插件顶部 `SOUND = "Glass"`，可换 `/System/Library/Sounds/` 下任意名。
- **改标题**：修改 `TITLE = "OpenCode"`。
- **改文案**：修改 `extractPermissionMsg` 和 `notify(TITLE, "任务已完成")` 处的字符串。
- **加更多终端 app**：在 `TERMINAL_BUNDLES` 数组中添加你的终端 bundle ID。

## 平台说明

仅 macOS（依赖 `osascript`/`lsappinfo`）。Linux 需将 `notify` 函数中的投递层改为
`notify-send`；Windows 改为 PowerShell `BurntToast`。事件结构与降级思路通用。

## 与已有 `plugin` 共存

opencode 的插件系统支持多个插件并行加载，所有 `event` 钩子顺序执行互不干扰。
无需担心与其他插件冲突。
