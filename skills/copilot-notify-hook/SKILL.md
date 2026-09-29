---
name: copilot-notify-hook
description: >
  为 GitHub Copilot CLI 配置任务完成和需要处理通知：macOS/WSL 使用 CLI 原生系统通知，
  所有平台可通过用户级 hook 发送飞书卡片。用户提到 Copilot CLI 完成通知、授权提醒、
  agentStop、notification 或 ~/.copilot/hooks 时使用。
---

# GitHub Copilot CLI Notify Hook

GitHub Copilot CLI 1.0.78 已支持用户级 hook。`agentStop` 在主代理完成一轮时触发；
`notification` 可按 `permission_prompt|elicitation_dialog` 过滤需要用户处理的事件。

系统通知直接使用 Copilot CLI 的 `notifications` 设置；它会在终端已聚焦时自动静默。
自定义 hook 只补充飞书投递，使用与 Codex、Claude Code、OpenCode、Cursor 相同的协议：
自建应用 IM API 优先，webhook 失败回退，完成卡片为蓝色，需处理卡片为橙色，均无按钮。

## 工作原理

```text
Copilot CLI 一轮结束 / 需要授权或补充信息
  -> agentStop / notification hook（~/.copilot/hooks/notify.json）
  -> notify.py
  -> 飞书卡片（系统通知由 Copilot CLI 自身的 notifications 设置负责）
```

## 安装步骤

### 0. 先检查环境

```bash
case "$(uname -s)" in
  Darwin) echo 'macOS：原生系统通知 + 飞书通知' ;;
  Linux)
    if [ -n "${WSL_INTEROP:-}" ] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
      echo 'WSL：原生系统通知 + 飞书通知'
    else
      echo 'Linux：仅飞书通知'
    fi
    ;;
  *) echo '不支持本地通知；仅在已配置时发送飞书通知' ;;
esac
copilot --version   # 需 1.0.78 及以上
```

### 1. 放置脚本并注册 hook

```bash
mkdir -p ~/.copilot/hooks
cp <skill-dir>/scripts/notify.py ~/.copilot/hooks/notify.py
cp <skill-dir>/hooks.json ~/.copilot/hooks/notify.json
chmod +x ~/.copilot/hooks/notify.py
```

`notify.json` 已含 `agentStop` 与 `notification` 两个注册，无需再改 `settings.json` 的 hook 字段。不要再注册 `sessionEnd`，否则退出会重复发送完成通知。

### 2. 开启原生系统通知（仅 macOS/WSL）

读取 `~/.copilot/settings.json`，合并 `"notifications": true`，不要覆盖已有设置。原生 Linux 不添加该项。

### 3. 配置飞书

凭证默认读取 `~/.copilot/feishu-agent.env`。`scripts/create_feishu_agent_app.py` 是完整、独立的配置入口：先使用 Copilot 自己的完整配置，再自动把其他 Agent 的完整应用和目标群配置复制成 Copilot 自己的 mode-`600` env；所有 Agent 都没有配置时，扫码创建或选择自建应用并写入完整 env。运行期不依赖其他 Skill，不创建软链接。

```bash
python3 <skill-dir>/scripts/create_feishu_agent_app.py
python3 -m pip install 'lark-oapi>=1.5.5'
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --home-channel oc_xxx --test
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --app-id cli_xxx --home-channel oc_xxx
python3 <skill-dir>/scripts/create_feishu_agent_app.py --live --new --home-channel oc_xxx
python3 <skill-dir>/scripts/create_feishu_agent_app.py --manual
```

新建应用前必须提供目标会话的 `chat_id`，创建后把机器人加入该会话。`--test` 发送一张连接测试卡片。`scripts/write_feishu_agent_env.sh` 只作手工兜底。不要在对话中传递密钥。

### 4. 生效

重启 Copilot CLI 后加载 hook。

## 验证

```bash
python3 -m json.tool <skill-dir>/hooks.json >/dev/null
bash <skill-dir>/scripts/test_notify.sh
python3 <skill-dir>/scripts/test_feishu_setup.py
copilot --version
```

预期测试输出 `ok: completion and attention payloads parsed`。飞书实发需在凭证就绪后，
分别完成一轮任务并触发一次授权或补充信息请求验证。
