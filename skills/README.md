# Skills

涉及 Python 的 Skill 先按[运行手册](INSTALLATION.md#21-pythonvenvuv-预检)执行环境预检；独立分发包随附同一检查脚本。

首次配置时动态获取官方当时的最新稳定应用版本；已有安装复用当前版本，明确升级时重新查询。运行平台保留兼容范围。

从 [安装运行手册](INSTALLATION.md)选择客户端、系统和安装作用域。每个 Skill 都可独立复制，完整安装步骤与飞书分支随包保留在 `references/`。

| Skill | 入口 |
|---|---|
| Codex 完成通知 | [codex-notify-hook](codex-notify-hook/SKILL.md) |
| Claude Code 通知 | [claude-code-notify-hook](claude-code-notify-hook/SKILL.md) |
| Cursor 完成通知 | [cursor-notify-hook](cursor-notify-hook/SKILL.md) |
| Copilot CLI 通知 | [copilot-notify-hook](copilot-notify-hook/SKILL.md) |
| OpenCode 通知 | [opencode-notify-hook](opencode-notify-hook/SKILL.md) |
| Codex 只读用量 | [codex-usage-status](codex-usage-status/SKILL.md) |
| Obsidian 知识库规范 | [obsidian-vault-governance](obsidian-vault-governance/SKILL.md) |

所有示例使用占位值。飞书应用创建和测试投递在目标账户完成，源码、分发包和验收记录均不保存真实私人信息。
