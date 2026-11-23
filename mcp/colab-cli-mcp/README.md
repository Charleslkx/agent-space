# Google Colab CLI MCP

把 Google Colab CLI 的会话管理、远程 Python 执行、文件传输和日志命令通过 Streamable HTTP MCP 提供给客户端。MCP 入口采用 GitHub OAuth 和账号白名单。

## 1. 部署入口

Ubuntu／Debian 部署从 [Linux 部署运行手册](../DEPLOYMENT.md) 开始。解压已审核的分发包并保留完整的 `mcp/` 目录。从 `/opt/mcp/colab-cli-mcp` 执行部署命令。

| 项目 | 值 |
|---|---|
| 服务名 | `colab-cli-mcp` |
| 公开端点 | `https://colab.example.com/mcp` |
| 宿主机监听 | `127.0.0.1:8770` |
| MCP 环境文件 | `/etc/colab-cli-mcp.env` |
| Redis 环境文件 | `/etc/colab-cli-mcp.redis.env` |
| Google 凭据文件 | `/etc/colab-cli-mcp/adc.json` |
| 配置前缀 | `COLAB_MCP` |

Google ADC 准备和真实 Colab 验收见[配置文档](CONFIGURATION.md)。通过白名单授权的客户端共用部署时配置的 Google 账号、运行时、日志和计算额度。工具调用契约见 [AGENTS.md](AGENTS.md)。

## 2. 运维

首次配置通过共享 Python 准备脚本从 PyPI 解析当时的稳定 Colab CLI 和 MCP 依赖，测试后写入 `deploy/python/`。重建沿用记录。升级时运行手册第 7 节的 `prepare-python.py --upgrade` 同时解析 CLI 和其他 Python 依赖。

应用日志、备份和回滚见[统一运行手册](../DEPLOYMENT.md#7-运维备份与回滚)。保留 Colab 状态卷、ADC 文件、OAuth 存储密钥与 Redis 卷的备份。CLI 历史可能包含用户代码与输出，选择归档或清理周期时按业务数据处理。

## 3. 开发验证

预检 Python、PATH、venv、ensurepip 和环境内 pip。仓库锁文件用于复现开发检查。测试环境可以放在临时目录。

```bash
bash ../scripts/check-python.sh --setup
bash ../scripts/check-python.sh --uv
test_root=$(mktemp -d)
UV_PROJECT_ENVIRONMENT="$test_root/venv" UV_CACHE_DIR="$test_root/cache" uv sync --locked --no-dev --python 3.12
"$test_root/venv/bin/python" -B -m unittest discover -s tests -v
COLAB_MCP_ENV_FILE=.env.example COLAB_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config --quiet
```

示例测试使用模拟授权和临时状态，真实 Google 后端的 CPU 会话验收在目标服务器进行。命令能力依据见 [Google 发布说明](https://developers.googleblog.com/introducing-the-google-colab-cli/) 和 [官方 CLI](https://github.com/googlecolab/google-colab-cli)。
