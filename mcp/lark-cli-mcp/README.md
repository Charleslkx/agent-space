# Lark CLI MCP

通过 GitHub OAuth 提供飞书业务命令、内嵌 Skill 与分步用户授权；白名单用户共享服务器飞书身份。

## 1. 部署入口

Ubuntu／Debian 统一按 [Linux 部署运行手册](../DEPLOYMENT.md)执行。必须复制完整 `mcp/` 目录到 `/opt/mcp/`，保留共享 `scripts/`、隐藏示例文件和锁文件。从 `/opt/mcp/lark-cli-mcp` 执行命令。

| 项目 | 值 |
|---|---|
| 服务名 | `lark-cli-mcp` |
| 公开端点 | `https://实际域名/mcp` |
| 环境文件 | `/etc/lark-cli-mcp.env` |
| 配置前缀 | `LARK_CLI_MCP` |
| 配置详情 | [CONFIGURATION.md](CONFIGURATION.md) |

完整流程包含独立 GitHub OAuth App、服务启动、Nginx 模板渲染、TLS 证书、OAuth 和真实工具调用验收。仅启动进程不能视为部署完成。各服务配置与工具边界见配置详情；Agent 工具契约见 [AGENTS.md](AGENTS.md)。

首次安装获取官方最新稳定版，执行 `sudo bash scripts/prepare-cli.sh` 保存本机版本记录后构建；已安装后的调试、修改和重建沿用该版本。显式执行 `sudo scripts/update-cli.sh` 才升级。

## 2. 运维入口

日志、更新、备份和回滚见运行手册第 7 节。修改凭据后重建容器或重启 systemd 服务；保持存储密钥与持久数据配套。升级 CLI 使用本项目已有的管理脚本（若提供），版本与测试通过后再更新生产实例。

首次安装自动选择官方最新稳定 CLI／应用依赖；已有实例沿用本机记录，只有明确升级才重新解析。运行平台保留兼容范围。

## 3. 开发验证

先验证 Python／venv／环境内 pip；准备脚本自行管理隔离 uv，不依赖全局 uv。缺项修复见[统一预检](../DEPLOYMENT.md#21-python-与非默认工具预检)。

```bash
bash ../scripts/check-python.sh --setup
python3 ../scripts/prepare-python.py lark-cli-mcp --native
deploy/python/.venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
LARK_CLI_MCP_ENV_FILE=.env.example LARK_CLI_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config --quiet
```

人类调用说明见 [USAGE.md](USAGE.md)。已有 Caddy 的交互式辅助流程见配置详情，使用该流程时沿用现有 Caddy 入口。
