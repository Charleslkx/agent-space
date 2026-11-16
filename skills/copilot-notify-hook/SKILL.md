---
name: copilot-notify-hook
description: 配置或排查 GitHub Copilot CLI 的 agentStop／notification 飞书通知，系统通知交给 CLI。
---

# GitHub Copilot CLI 通知

涉及 Python 的操作先按[安装预检](references/installation.md#11-python-与工具预检)验证环境；运行检查用 `--runtime`，SDK 安装用 `--setup`。未通过时先补齐依赖，不继续写配置。

## 1. 范围与系统

系统通知由 Copilot CLI 处理；本 Skill 的脚本只负责飞书。 `notification` 不在 Copilot 云端 Agent 触发。本方案支持具备用户级 hooks 的 CLI，先核对当前版本与官方事件定义。

原生 Linux 需要完成飞书配置才能产生通知；没有投递通道的 hook 不计为安装验收成功。

## 2. 前提与 Skill 安装

首次安装先按 [installation.md](references/installation.md)完成系统依赖和 Skill 发现。需要 Bash 3.2+、Python 3.10+（应用配置／Python 投递）；已有客户端先验证版本，GUI 安装没有命令行入口时在其版本页核对，并确保 hook 子进程能找到 Bash／Python。

```bash
copilot --version
python3 --version
# 替换为当前实际包含 SKILL.md 的目录，路径含空格时保留双引号。
export SKILL_DIR="/path/to/installed/copilot-notify-hook"
```

## 3. 运行文件与注册

修改前将现有运行文件及相关注册备份到客户端扫描目录之外的私有目录；只替换本 Skill 的文件及注册。

```bash
mkdir -p "$HOME/.copilot/hooks"
cp "$SKILL_DIR/scripts/notify.py" "$HOME/.copilot/hooks/notify.py"
chmod +x "$HOME/.copilot/hooks/notify.py"
```

将随包 `hooks.json` 的 `agentStop`、`notification` 定义合并到 `~/.copilot/hooks/notify.json`；文件不存在时才直接复制：

```bash
if [ ! -e "$HOME/.copilot/hooks/notify.json" ]; then
  cp "$SKILL_DIR/hooks.json" "$HOME/.copilot/hooks/notify.json"
fi
```

`agentStop` 表示主代理一轮完成；`notification` 只选择 `permission_prompt|elicitation_dialog`。macOS／WSL 在 `~/.copilot/settings.json` 合并 `"notifications": true`，本脚本仅发飞书。保留其他注册，避免将本通知重复挂到 `sessionEnd`。重启 CLI 后加载。

配置格式依据 [官方文档](https://docs.github.com/en/copilot/reference/hooks-reference)。GUI／后台客户端不会自动继承交互终端的 PATH；运行环境缺少 Python／Bash 时，在该处理器的启动环境中加入实际安装目录，或使用这些可执行文件的绝对路径，重新验证加载。

## 4. 飞书应用与通道

飞书为可选通道，需要时读取随包的 [feishu-setup.md](references/feishu-setup.md)，先运行只读 `--check` 验证 SDK 与 `lark-cli` 能力，再选择复用、手工配置或 SDK 扫码创建。通知运行不依赖 CLI 或用户登录态；创建应用、改动已有应用与发送测试卡片按用户授权的范围执行。

env、App ID／Secret、chat_id、webhook 和授权 URL 只保存在目标账户的私有运行环境，源码、示例和部署报告使用占位符。卡片只展示通知，不包含审批按钮。

## 5. 验收与排障

```bash
bash "$SKILL_DIR/scripts/test_notify.sh"
python3 "$SKILL_DIR/scripts/test_feishu_setup.py"
```

离线测试使用替身，不发送真实通知。安装后重新加载客户端，触发一次完成事件；支持待处理事件的客户端再触发一次。启用飞书的实例按配置参考单独验证真实卡片投递。

未投递时依次检查：客户端加载／信任、当前作用域与账户、事件状态、子进程 PATH、焦点过滤、飞书 env／租户权限。`NOTIFY_DEBUG=1` 输出诊断，`NOTIFY_FORCE=1` 可在受控测试时绕过焦点过滤。通知输出和错误不要污染协议 stdout。

## 6. 更新、回滚与卸载

先备份旧 Skill、运行文件和本通知的配置；更新完整 Skill 后重新复制第 3 节的运行文件、合并注册并重复验收。失败时只恢复本通知的备份，保留用户其他设置；Codex 等客户端按提示重新审阅定义。

卸载移除本通知的注册、独占运行文件和 Skill 目录。共享机器人和其他 Agent 的私有 env 不随之删除。完整安装与目录约定见 [installation.md](references/installation.md)。
