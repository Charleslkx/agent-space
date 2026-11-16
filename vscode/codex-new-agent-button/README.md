# Codex New Agent Button

为 VS Code／兼容宿主的编辑器标题栏提供 Codex 按钮，依赖官方 `openai.chatgpt` 扩展。

## 1. 安装与使用

```bash
code --install-extension ./codex-new-agent-button-0.0.14.vsix
```

命令在本目录执行；远程工作区需在实际运行扩展的宿主安装。当前包版本以 `package.json` 为准。

| 按钮／命令 | 行为 |
|---|---|
| New Codex Agent（花形图标） | 调用官方新会话命令 |
| Resume Codex Session（历史图标） | 从本机 Codex 数据库列出会话，使用 sqlite3 或 Python 标准库读取，再交给官方扩展打开 |
| Exit Codex Session（退出图标） | 关闭当前会话标签，让官方扩展处理退订，并清理不再被进程持有的 writer 锁；不退出账号登录 |
| Release Stale Codex Locks | 手动清理未被持有的 writer 锁 |

## 2. 设置与迁移

`codexNewAgentButton.exitOnEditorClose` 和 `codexNewAgentButton.releaseStaleWriterLocks` 默认均为 `true`，分别控制关闭标签后的退出处理和过期锁清理；活跃锁保持不变。

`0.0.14` 移除了旧共享 daemon 包装器。激活时清理旧 `useSharedDaemon` 偏好；若 `chatgpt.cliExecutable` 仍指向该包装器，恢复已记录的旧值，无法恢复时清除覆盖。曾安装旧版本的本地和远程宿主都需升级。

官方扩展负责 App Server 生命周期；内部命令或数据库格式变化后，需重新验证兼容性。
