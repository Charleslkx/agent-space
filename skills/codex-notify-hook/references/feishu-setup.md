# 飞书通知配置

## 1. 配置分支与预检

五个通知 Skill 通过飞书应用身份发送卡片，不使用用户登录态。应用创建使用官方 Python SDK；通知投递使用标准库。`lark-cli` 用于可选应用管理，已有 `lark-cli` 配置不会自动变成通知所需的 env，也不需要为发卡片执行用户 `auth login`。

在同一 Bash 会话中，将 `SKILL_DIR` 设置为当前已安装 Skill 的真实路径：

```bash
SKILL_DIR=/path/to/installed/your-notify-skill
bash "$SKILL_DIR/scripts/check-python.sh" --runtime
python3 "$SKILL_DIR/scripts/create_feishu_agent_app.py" --check
```

凭据目标必须在 MCP／Skill 分发源目录之外，脚本在发起应用创建前拒绝不合法位置。

预检只执行 `lark-cli --version`、`config init --help` 和 SDK 能力检查，不读取 App Secret、不创建应用、不授权。检查返回的 `sdk.register_app`、`sdk.supports_setup`、`ready_for_sdk_creation`、`lark_cli.available` 和 `lark_cli.supports_app_creation`。

| 状态 | 后续路径 |
|---|---|
| 已有本机完整通知 env | 第 2 节复用，不要求 SDK 或 CLI |
| 已有 App ID／Secret，尚无 env | 第 3 节在真实终端配置，不要求 SDK 或 CLI |
| 需扫码创建或更新应用 | 第 4 节安装 SDK，再运行预检和创建流程 |
| 用户另有 CLI 管理需求 | 第 5 节安装／检查 CLI；不重复创建已有应用 |

## 2. 复用已有通知配置

```bash
python3 "$SKILL_DIR/scripts/create_feishu_agent_app.py"
```

脚本先检查当前 Skill 的 env，再从其他 Agent 的完整通知 env 复制；只写入自己的私有文件，不创建软链接，也不修改供体配置。更换会话可指定 `--home-channel your-chat-id`；指定现有应用可加 `--app-id your-app-id`。无可复用配置时只输出后续流程，不代表已经安装成功。

| Agent | 默认环境文件 |
|---|---|
| Codex | `~/.codex/feishu-agent.env`；自定义 `CODEX_HOME` 时同步指定 `--env-out` |
| Claude Code | `~/.claude/feishu-agent.env` |
| Cursor | `~/.cursor/feishu-agent.env` |
| Copilot CLI | `~/.copilot/feishu-agent.env` |
| OpenCode | `~/.config/opencode/feishu-agent.env` |

使用当前运行客户端的账户，不复制其他服务器或服务账户的文件。只有 App ID、Secret 和目标会话三项完整才可复用；真实值不进入仓库或部署报告。

## 3. 已有应用手工配置

先在飞书开放平台为该应用开启机器人能力，授予 `im:message:send_as_bot`，发布并完成租户所需审批，将机器人加入目标会话。`chat_id` 可从已有会话链接或已授权的应用查询中取得；缺失时向用户获取，不猜测标识。

```bash
python3 "$SKILL_DIR/scripts/create_feishu_agent_app.py" --configure
```

由操作者在真实终端依次输入 App ID、隐藏输入的 App Secret、目标 chat_id 和 `feishu`／`lark`。脚本原子写入权限为 `0600` 的 env，保留无关字段，不将凭据打印出来。`scripts/write_feishu_agent_env.sh` 是同一流程的兼容入口；不编辑源码填秘密，也不把 Secret 作为命令参数。

## 4. SDK 扫码创建或更新应用

先通过 Python 运行预检；SDK 安装分支还需通过 `--setup` 并检查已有 SDK 虚拟环境内 pip。缺项先修复环境，未通过时不创建应用、不写凭据。此流程不要求全局 uv。

SDK 只供配置阶段使用，`requirements-setup.txt` 不指定版本，首次配置时实时从官方 PyPI 获取当时的最新稳定 `lark-oapi`；仅有 `register_app` 而缺少创建参数的版本不满足本流程。将虚拟环境放在用户私有状态目录，避免污染系统 Python 或分发包：

```bash
bash "$SKILL_DIR/scripts/check-python.sh" --setup
SETUP_ENV="$HOME/.local/share/agent-notify/setup-venv"
if [ ! -x "$SETUP_ENV/bin/python" ]; then
  python3 -m venv "$SETUP_ENV"
fi
"$SETUP_ENV/bin/python" -m pip --version
"$SETUP_ENV/bin/python" -m pip --isolated install --index-url https://pypi.org/simple -r "$SKILL_DIR/requirements-setup.txt"
"$SETUP_ENV/bin/python" "$SKILL_DIR/scripts/create_feishu_agent_app.py" --check
"$SETUP_ENV/bin/python" "$SKILL_DIR/scripts/create_feishu_agent_app.py" --live --new --home-channel your-chat-id
```

创建前须已获得用户创建应用的同意；用实际目标 chat_id 替换占位符。SDK 创建流程会等待用户完成浏览器扫码，Agent 应保留同一后台进程，提供 URL／二维码后等待用户完成，再读取该进程结果；不重复创建。取消或过期时先确认进程结束。

`--new` 强制新建独立应用；需要更新已有应用时改用 `--live --app-id your-app-id`，不要同时传 `--new`。更新只增加所需配置，不自动撤销该应用已有的其他权限。脚本请求最小基线和发送机器人消息权限，不注册事件接收或按钮回调。SDK 完成后仍需核对机器人能力、发布状态、租户审批及入群结果。

已安装后调试和修改时不添加 `--upgrade`、不重建虚拟环境。明确升级 SDK 时，先备份该虚拟环境，再运行以下命令并通过参数预检及卡片测试；失败时将备份恢复到原路径：

```bash
"$SETUP_ENV/bin/python" -m pip --version
"$SETUP_ENV/bin/python" -m pip --isolated install --index-url https://pypi.org/simple --upgrade --upgrade-strategy eager -r "$SKILL_DIR/requirements-setup.txt"
"$SETUP_ENV/bin/python" "$SKILL_DIR/scripts/create_feishu_agent_app.py" --check
```

## 5. lark-cli 可用性与应用管理

预检无 CLI 时，SDK／手工分支仍可工作。首次确需安装 CLI 管理工具时实时获取官方当时的最新稳定版；已安装后的调试、配置和代码修改沿用当前版本，不重新运行 latest 安装或 update。只有明确升级时才换版。使用 [官方安装说明](https://github.com/larksuite/cli)，例如在已有 Node.js／npm 环境中执行官方安装向导：

```bash
node --version
npm --version
if ! command -v lark-cli >/dev/null 2>&1; then
  npx @larksuite/cli@latest install
fi
lark-cli --version
lark-cli config init --help
```

安装向导可能继续询问 Skill 安装、应用和用户授权，按当前任务选择，已有通知应用不重复创建。需要 CLI 的新应用流程时先检查当前账户的既有配置，并确认 `--new` 可用，再按官方指引完成浏览器步骤；不要以 `config show` 的输出收集或打印 Secret。

通知 env 仍由第 2／3／4 节生成；本 Skill 不自动导出 CLI 凭据。CLI 如需读取业务资源，另按对应权限完成用户授权，与通知机器人配置分开验收。

Codex 的持久 webhook 回退在真实终端运行以下命令，隐藏输入 URL／签名密钥，不编辑源码：

```bash
bash "$SKILL_DIR/scripts/write_feishu_webhook_env.sh"
```

它写入 `~/.codex/feishu-webhook.env`（`0600`）。其他客户端从其实际运行环境接收 webhook 变量；GUI 启动不会自动继承终端导出值，因此优先使用已经配置的应用 env 作为稳定通道。

## 6. 通道验收与运维

```bash
# 已配置凭据后实际发送一张卡片；无需 SDK 时用系统 python3。
python3 "$SKILL_DIR/scripts/create_feishu_agent_app.py" --test
```

返回成功且目标会话收到卡片才算投递验收通过；随后通过对应客户端事件验证 hook／插件加载。预检、离线测试或仅发现 env 都不能替代发送验收。

卡片使用应用身份，不带审批按钮。`FEISHU_HOME_CHANNEL` 为目标会话，兼容 `FEISHU_APPROVAL_RECEIVE_ID`；`FEISHU_APPROVAL_RECEIVE_ID_TYPE` 默认 `chat_id`。轮换凭据后更新每个 Agent 自己的 env，再重测；没有后台常驻 SDK 服务。

| 故障 | 检查 |
|---|---|
| 无 Python／SDK 能力 | 检查当前解释器，按第 4 节安装当前兼容范围内的最新稳定依赖 |
| CLI 缺失、超时或不支持 `--new` | 仅阻断 CLI 管理分支，修复 PATH／版本后重跑预检 |
| 扫码失败／过期 | 确认原进程已退出，检查网络后重新发起一次 |
| 飞书权限或会话错误 | 检查机器人能力、发送权限、发布审批、品牌、目标 chat_id 和入群状态 |
| 卡片测试成功但客户端不通知 | 检查当前账户的注册、客户端重载／信任、事件状态及焦点过滤 |
| webhook 未生效 | 运行进程需得到 `LARK_WEBHOOK_URL`／`LARK_WEBHOOK_SECRET`；Codex 另支持私有 webhook env，勿将真实 URL 写入注册或源码 |

SDK 接口依据 [飞书扫码创建应用](https://open.feishu.cn/document/mcp_open_tools/scan-to-create-an-app-in-one-click)。应用和会话标识、凭据及授权 URL 均为本机运行数据，不回写 Skill、示例或验收记录。
