# Skills 安装运行手册

版本策略按执行时点生效：未来首次配置时实时查询官方当时的最新稳定版，不以本文编写日期或仓库开发锁文件中的版本作为安装默认值。已有安装保留当前版本，明确升级时才重新查询。

## 1. 范围与输入

本仓库按 Agent Skills 目录格式分发：每个 Skill 包含 `SKILL.md` 及随附脚本、参考和元数据。安装 Skill 只使客户端能发现它；通知类还需按该 Skill 的指引安装运行脚本并注册 hook／插件。

| Skill | 对应客户端 | 功能与额外依赖 |
|---|---|---|
| `codex-notify-hook` | Codex | 完成通知；Bash、Python |
| `claude-code-notify-hook` | Claude Code | 完成／待处理通知；Bash、Python |
| `cursor-notify-hook` | Cursor | 完成／异常通知；Bash、Python |
| `copilot-notify-hook` | Copilot CLI | 飞书通知；Python，原生通知由客户端处理 |
| `opencode-notify-hook` | OpenCode | 完成／权限通知；插件运行时，配置脚本需 Python |
| `codex-usage-status` | Codex | 只读账户用量；Python 和可用的 Codex CLI |
| `obsidian-vault-governance` | 支持 Skills 的客户端 | Obsidian 知识库规范；无额外运行依赖 |

部署输入为：目标客户端、Skill 名称、用户级或项目级作用域、安装源路径。通知另需决定本地／飞书通道；飞书输入为自建应用、目标会话 `chat_id`、品牌和应用凭据。所有 `your-*`、`/path/to/...` 均为待替换占位符。

所有通知 CLI／SDK 首次安装从官方来源获取最新稳定版；已安装后的调试和代码修改沿用现有版本，只有明确升级才重新解析。Python、Redis、BusyBox 等运行平台保留兼容范围。

## 2. 系统与依赖

支持 macOS、Ubuntu／Debian 和 WSL2。通知安装在**实际运行客户端的机器和账户**下：SSH／云端的用户级目录独立于本机。原生 Windows 不使用本仓库 Bash 通知方案；Windows 使用 WSL2。Linux 服务端无桌面时使用飞书。

运行安装和 Python 配置脚本需要 Python 3.10+。Bash 通知还需要 `basename`、`grep`、`sed`、`cat` 等系统命令；`jq` 改善 JSON 解析。macOS 本地通知用系统 `osascript`，可选 `terminal-notifier`；WSL 的本地通知需要启用 Windows 互操作且可调用 `powershell.exe`。

```bash
python3 --version
bash --version
# Ubuntu／Debian 的依赖；客户端软件另按其官方安装说明准备。
sudo apt-get update
sudo apt-get install -y python3 python3-venv bash jq ca-certificates curl
```

macOS 已有合适版本时不重复安装；缺少 Python／jq 时使用现有包管理器，例如 `brew install python jq`。OpenCode 的插件由其内置运行时执行；单独运行文档中的离线结构检查需 Node.js 18+。

### 2.1. Python／venv／uv 预检

在仓库根目录安装 Python Skill 前执行：

```bash
bash skills/scripts/check-python.sh --runtime
# 仅安装 SDK 时需要；纯标准库查询和通知不要求 venv/pip。
bash skills/scripts/check-python.sh --setup
```

独立 Skill 使用随包 `scripts/check-python.sh`，详细步骤见其 `references/installation.md`。`--runtime` 验证 Python 3.10+ 与标准库；`--setup` 实际创建临时虚拟环境并验证环境内 pip，不要求系统 pip。已有 SDK 环境还须运行该解释器的 `-m pip --version`。缺失时先按上节安装系统依赖，修复后重跑，未通过不写配置或发起应用创建。

本仓库 Skill 不要求全局 uv；自定义流程确需 uv／uvx 时，先执行 `bash skills/scripts/check-python.sh --uv`，缺失则按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装，并验证实际客户端子进程的 PATH。预检不升级既有解释器或工具。纯 Markdown Skill 跳过 Python 检查。

## 3. 安装位置与命令

| 客户端 | 默认用户级父目录 | 项目级父目录 |
|---|---|---|
| Codex | `~/.agents/skills/` | `.agents/skills/` |
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Cursor | `~/.cursor/skills/` | `.cursor/skills/` |
| Copilot CLI | `~/.copilot/skills/` | `.github/skills/` |
| OpenCode | `~/.config/opencode/skills/` | `.opencode/skills/` |

在仓库根目录执行，以 Codex 为例：

```bash
python3 skills/scripts/install_skill.py --skill codex-notify-hook --agent codex
# 项目级安装，项目路径替换为实际值。
python3 skills/scripts/install_skill.py --skill codex-notify-hook --agent codex --project /path/to/project
```

二选一，避免同名 Skill 重复发现。安装器不修改 hook、客户端设置、飞书应用或凭据；排除环境文件、密钥、缓存和运行数据，不接受指向私人工作区的分发软链接。已有同名目录默认拒绝覆盖。

只获得单个 Skill 文件夹时，按其 `references/installation.md` 手工复制；完整保留 `scripts/`、`references/`、`agents/` 和依赖文件。自定义客户端配置根目录时按实际路径安装并修改注册命令，不假定默认路径仍生效。

## 4. 配置与验收

1. 重启或重新加载目标客户端，确认 Skill 名称与文件路径正确。Codex 可通过 `/skills` 查看，Claude Code 通过对应 `/skill-name` 调用，Copilot CLI 使用 `/skills reload` 和 `/skills info skill-name`；其他客户端使用其 Skills 界面或工具。
2. 按所选 `SKILL.md` 安装 hook／插件。只合并该 Skill 的注册，保留其他 hooks；需要信任时按客户端的实际提示完成。
3. 涉及飞书时读取 Skill 内的 `references/feishu-setup.md`，先运行 `create_feishu_agent_app.py --check`。报告包括 SDK 及 `lark-cli` 可用性／`config init --new` 能力，不读出应用凭据。通知运行不依赖 CLI；SDK 创建、已有应用手工配置和 CLI 管理是独立分支。
4. 运行离线测试后，完成一次对应客户端事件；启用飞书的实例再发送一张测试卡片。离线成功不能替代真实客户端加载和租户权限验收。

记录系统、客户端及 Python／SDK／CLI 版本、Skill 安装位置、hook 配置路径和验收结果。部署记录不包含 App ID、会话标识、凭据、扫码 URL 或授权码等实际私人数据。

## 5. 更新、回滚与卸载

```bash
python3 skills/scripts/install_skill.py --skill codex-notify-hook --agent codex --upgrade
```

更新会把旧 Skill 移入**父目录同级**的 `skill-backups/`，其目录权限为 `0700`；切换失败恢复旧目录。更新 Skill 后，还需按 `SKILL.md` 重新复制运行脚本、检查 hook 并重新验收。依赖的虚拟环境放在客户端私有状态目录，不放入 Skill 分发目录。

修改 hook 设置前复制到客户端私有备份目录。回滚时恢复旧 Skill、运行文件和那份 hook 配置；不要整体回滚用户之后新增的其他设置。卸载只移除当前通知注册及其独占运行文件、Skill 目录；共享机器人和其他 Skill 的环境文件保持不动，除非用户另行要求清理。

## 6. 分发与检查

`mcp/` 和 `skills/` 的分发源不得包含真实凭据、个人目录、邮箱、会话或租户标识。示例使用 `example.com`、`your-app-id`、`your-chat-id` 等占位符；公共上游地址、回环地址和产品 bundle ID 保留原义。每次发布前检查最终源文件与 diff，不把依赖缓存或本机配置打包。

```bash
python3 -B skills/scripts/check_public_sources.py
python3 -B -m unittest discover -s skills/tests -v
```

MCP 压缩包另按[部署手册](../mcp/DEPLOYMENT.md#91-生成并审核分发包)从已审核提交生成，并对实际归档运行 `check_public_sources.py --archive`。

格式与发现路径依据 [Agent Skills](https://agentskills.io/specification)、[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[Cursor](https://cursor.com/docs/skills)、[Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) 和 [OpenCode](https://opencode.ai/docs/skills/) 官方文档；客户端更新后以其实际能力和加载结果验收。
