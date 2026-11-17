---
name: codex-usage-status
description: 查看当前 ChatGPT 登录态下的 Codex 用量、剩余比例，以及 5 小时、每周和其他限额窗口的准确刷新时间。用户询问 Codex usage、rate limit、额度、用量百分比、5h/weekly 窗口或何时恢复时使用。
---

# Codex 用量状态

查询实时账户状态，不复用先前对话中的数值。

涉及 Python 的操作先按[安装预检](references/installation.md#11-python-与工具预检)验证环境；运行检查用 `--runtime`，SDK 安装用 `--setup`。未通过时先补齐依赖，不继续写配置。

## 1. 前提与安装

按 [installation.md](references/installation.md)安装完整 Skill。支持 macOS、Linux 和 WSL2，需 Python 3.10+ 及能运行 `app-server --stdio` 的 Codex CLI，无第三方 Python 依赖，也不需要飞书或 lark-cli。

```bash
python3 --version
codex --version
codex app-server --help
export SKILL_DIR="/path/to/installed/codex-usage-status"
bash "$SKILL_DIR/scripts/check-python.sh" --runtime
```

需由当前用户事先通过 Codex 的正常流程完成 ChatGPT 账户登录；不复制其他人的认证文件。通过客户端发现列表确认 Skill 可见。

## 2. 查询

运行随附的只读脚本：

```bash
python3 "$SKILL_DIR/scripts/codex_usage_status.py"
```

默认使用系统本地时区；对话明确提供用户时区时，通过 `--timezone` 传入对应 IANA 名称：

```bash
python3 "$SKILL_DIR/scripts/codex_usage_status.py" --timezone Asia/Shanghai
```

需要机器可读结果时使用 `--json`；`--timeout` 指定总查询超时秒数，默认 30。

脚本通过本机 `codex app-server --stdio` 调用 `account/rateLimits/read`，应报告：

- 查询时间和套餐类型；
- 每个 limit ID 下的全部窗口；
- 窗口时长、已用/剩余比例、准确刷新时间和倒计时；
- credits 状态和可用完整重置次数（如后端返回）。

将 `300` 分钟窗口称为“5 小时窗口”，将 `10080` 分钟窗口称为“每周窗口”；其他时长保持明确标注，不自行猜测产品含义。

## 3. 输出与失败处理

- 此技能仅查看状态。绝不调用 `account/rateLimitResetCredit/consume`，也不替用户使用免费或付费重置。
- 不展示 account ID、访问令牌、reset-credit ID 或其他认证信息。
- 如果当前不是 ChatGPT 登录态、`codex` 不可用或查询超时，说明具体错误；不要读取或打印本地凭据来绕过失败。
- 若协议查询不可用，指导用户在 Codex CLI 会话运行 `/status`，或打开 Settings → Usage。不要把 API RPM/TPM 限额误当作 ChatGPT 套餐的 Codex 5 小时窗口。

回答时优先给出最接近的刷新时间，再列用量与其他窗口。明确使用的时区，并注明这是查询时快照。

## 4. 验收与维护

运行一次只读查询，确认返回查询时间、时区和窗口；无法访问账户时报告实际原因，不编造用量。部署记录只保存能力验证结果，不保存账户标识或实际账户响应。

更新替换完整 Skill 并重新验证只读查询；回滚恢复旧目录，卸载只删除 Skill 目录，不退出账号或删除认证文件。安装、更新备份约定见 [installation.md](references/installation.md)。
