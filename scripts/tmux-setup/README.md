# tmux setup

这是一套可分发的 tmux 配置和安装脚本，当前固化的配置包括：

- TPM、tmux-sensible、tmux-resurrect、tmux-continuum、tmux-yank
- Dracula 主题及 CPU、内存、时间状态栏组件
- continuum 自动保存，但关闭自动恢复（`@continuum-restore off`）
- SSH 场景下优先使用 `tmux-256color`，缺少 terminfo 时回退到 `screen-256color`
- 鼠标、焦点事件和较大的滚动历史限制

## 快速使用

在仓库目录中运行：

```sh
./scripts/tmux-setup/tmux-setup.sh
```

新机器上希望全自动执行时：

```sh
./scripts/tmux-setup/tmux-setup.sh --yes
```

脚本会先检查 `tmux` 和 `git`。缺少时，在 Ubuntu/Debian 使用 APT，在 macOS 优先使用 Homebrew、其次 MacPorts；也支持 DNF、YUM、Pacman、Zypper、APK。找不到包管理器时会明确提示手动安装命令，不会偷偷安装未知软件源。

已有 `~/.tmux.conf` 时，脚本只会在确认后替换，并先生成带时间戳的备份，例如 `~/.tmux.conf.backup.20260824-153000`。脚本可重复运行；配置内容未变化时不会重复备份。

插件通过 HTTPS 从 GitHub 克隆。首次运行使用浅克隆最新默认分支，后续运行对无本地改动的插件执行 `git pull --ff-only`；不固定版本号或提交。检测到插件目录有本地改动时会跳过更新并提醒，避免覆盖个人修改。

## 常用选项

```text
--yes          自动同意依赖安装和配置覆盖
--force        自动同意配置覆盖
--no-install   缺少 tmux/git 时只提示，不安装
--no-reload    不重载正在运行的 tmux server
--dry-run      只检查和显示计划，不改文件、不联网安装
--config PATH  使用其他 tmux 配置路径
```

脚本校验配置后，如果当前有 tmux server 会自动执行 `source-file`；没有运行中的 server 时，新会话会自动读取新配置。

## 前置条件和 fallback

- 需要 Bash 和 Git；macOS 自带 Bash 3.2 也可运行。
- 包管理器安装可能需要 `sudo`；无 `sudo` 或非交互环境无法输入密码时，脚本会失败并给出原因。
- 无网络、GitHub 不可达或插件仓库更新失败时，配置仍会保留；脚本会以非零状态提醒未完成的插件，联网后可重试。
- 复制整个 `tmux-setup` 目录即可分发；脚本通过自身所在目录定位 `tmux.conf`，不依赖当前工作目录。
