# 单个 Skill 的安装

## 1. 系统与依赖

保留当前文件所在 Skill 的完整目录，包含 `SKILL.md`、脚本、参考、`agents/` 和依赖文件（存在时）。仅复制已检查的干净分发包，不复制本地 `.venv`、env、凭据或缓存。

支持 macOS、Ubuntu／Debian、WSL2；Python 脚本使用 Python 3.10+。Bash 通知需要 Bash 3.2+ 与常见系统命令，建议安装 `jq`。原生 Windows 使用 WSL2；纯 Markdown 的知识库规范没有系统依赖。

```bash
python3 --version
bash --version
# Ubuntu／Debian，已有依赖时跳过安装。
sudo apt-get update
sudo apt-get install -y python3 python3-venv bash jq ca-certificates curl
```

macOS 先使用已有 Python；缺失时使用现有包管理器安装，例如 `brew install python jq`。本地通知可用系统 `osascript`，`terminal-notifier` 为可选项。WSL Toast 需要 Windows 互操作和 `powershell.exe`。OpenCode 插件使用客户端内置运行时，单独运行结构测试需要 Node.js 18+。

### 1.1. Python 与工具预检

涉及 Python 的 Skill 在配置或查询前执行随包检查；先将 `SKILL_DIR` 指向实际 Skill 目录：

```bash
SKILL_DIR=/path/to/installed/your-skill-name
# 仅适用于带 scripts/check-python.sh 的 Python Skill；纯 Markdown Skill 跳过。
bash "$SKILL_DIR/scripts/check-python.sh" --runtime
```

检查 Python 3.10+ 和标准库能力；缺失时先修复解释器或当前账户的 PATH。需要安装飞书 SDK 时改用 `--setup`，实际验证 venv／ensurepip 及新环境内 pip。Ubuntu／Debian 缺项安装 `python3 python3-venv`（自选解释器安装对应 `python3.X-venv`）；macOS 使用包含这些能力的 Python。检查只创建可清理的临时环境，不修改已有依赖或配置。

这些 Skill 不依赖全局 uv。另行使用 uv／uvx 前用 `--uv` 检查可执行文件；缺失时按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装并重新检查 PATH，调试期间保留已有版本。纯 Markdown 的知识库规范没有 Python／uv 前置要求。

## 2. 安装位置

| 客户端 | 用户级父目录 | 项目级父目录 |
|---|---|---|
| Codex | `~/.agents/skills` | `.agents/skills` |
| Claude Code | `~/.claude/skills` | `.claude/skills` |
| Cursor | `~/.cursor/skills` | `.cursor/skills` |
| Copilot CLI | `~/.copilot/skills` | `.github/skills` |
| OpenCode | `~/.config/opencode/skills` | `.opencode/skills` |

在实际运行客户端的机器及账户安装，勿用 sudo 写入另一个账户的家目录。选择一个作用域，保留 Skill 目录名，避免重复发现。以用户级 Codex 为例，先替换源路径：

```bash
SOURCE_SKILL=/path/to/distribution/your-skill-name
TARGET_PARENT="$HOME/.agents/skills"
SKILL_NAME=$(basename "$SOURCE_SKILL")
TARGET_SKILL="$TARGET_PARENT/$SKILL_NAME"
test -f "$SOURCE_SKILL/SKILL.md"
test ! -e "$TARGET_SKILL"
mkdir -p "$TARGET_PARENT"
cp -R "$SOURCE_SKILL" "$TARGET_SKILL"
```

逐条执行，失败则停止。随后将 `SKILL_DIR` 设置为刚安装、实际包含 `SKILL.md` 的目录，返回主文件完成运行配置；仅复制 Skill 不会自动注册 hook。自定义客户端配置根目录时同步调整所有目标路径。

## 3. 验收、更新与卸载

重新加载客户端，确认名称和安装路径；按主文件执行离线测试，再验证实际功能。通知的桌面／飞书通道、Codex 账户访问等各有独立验收，不能互相替代。

更新前把旧 Skill、运行文件与相关注册备份到扫描目录之外的私有目录（目录 `0700`，凭据文件 `0600`），再替换分发代码和运行文件、合并注册并验收。回滚只恢复本 Skill 的修改；卸载只删除其注册和独占文件，保留其他设置与共享飞书应用。

## 4. 格式与路径依据

按 [Agent Skills](https://agentskills.io/specification)分发，发现路径见 [Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[Cursor](https://cursor.com/docs/skills)、[Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)、[OpenCode](https://opencode.ai/docs/skills/)。目标客户端实际加载结果为验收依据。
