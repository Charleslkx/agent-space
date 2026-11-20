# Firecrawl CLI MCP

将 Firecrawl CLI 的允许检索命令通过 GitHub OAuth 保护的远程 MCP 提供给客户端。

## 1. 部署入口

Ubuntu／Debian 统一按 [Linux 部署运行手册](../DEPLOYMENT.md)执行。从已审核的分发包解出完整 `mcp/` 目录，保留共享脚本、隐藏示例文件和锁文件。从 `/opt/mcp/firecrawl-mcp` 执行命令。

| 项目 | 值 |
|---|---|
| 服务名 | `firecrawl-mcp` |
| 公开端点 | `https://实际域名/mcp` |
| 环境文件 | `/etc/firecrawl-mcp.env` |
| 配置前缀 | `FIRECRAWL_MCP` |
| 配置详情 | [CONFIGURATION.md](CONFIGURATION.md) |

完整流程包含独立 GitHub OAuth App、服务启动、Nginx 模板渲染、TLS 证书、OAuth 和真实工具调用验收。仅启动进程不能视为部署完成。各服务配置与工具边界见配置详情；Agent 工具契约见 [AGENTS.md](AGENTS.md)。

## 2. 运维入口

日志、更新、备份和回滚见运行手册第 7 节。修改凭据后重建容器或重启 systemd 服务；保持存储密钥与持久数据配套。升级 CLI 使用本项目已有的管理脚本（若提供），版本与测试通过后再更新生产实例。

首次安装自动选择官方最新稳定 CLI／应用依赖；已有实例沿用本机记录，只有明确升级才重新解析。运行平台保留兼容范围。

## 3. 开发验证

先验证 Python／venv／环境内 pip；准备脚本自行管理隔离 uv，不依赖全局 uv。缺项修复见[统一预检](../DEPLOYMENT.md#21-python-与非默认工具预检)。

```bash
bash ../scripts/check-python.sh --setup
python3 ../scripts/prepare-python.py firecrawl-mcp --native
deploy/python/.venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
FIRECRAWL_MCP_ENV_FILE=.env.example FIRECRAWL_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config --quiet
```
