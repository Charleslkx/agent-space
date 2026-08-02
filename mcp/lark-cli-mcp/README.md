# Lark CLI MCP

把官方 `lark-cli` 的飞书业务命令通过 GitHub OAuth 保护的 Streamable HTTP MCP 提供给远程客户端。服务同时公开 CLI 内嵌、与当前版本一致的完整 Skill 文档体系。

## 工具

- `lark_cli(args, stdin?)`：执行允许的 `lark-cli` 命令，返回原始 stdout、stderr、退出码和超时状态。
- `lark_cli_skill(action, path?)`：逐层列出或读取 CLI 内嵌 Skill。

服务器保留一份共享飞书应用和用户登录态。GitHub OAuth 白名单中的用户都使用这份飞书身份，因此白名单只能包含互相信任的人。

## 部署入口

公网域名固定为 `lark.{基础域名}`。基础域名为 `example.com` 时，MCP 地址为：

```text
https://lark.example.com/mcp
```

完整部署步骤见 [CONFIGURATION.md](CONFIGURATION.md)，人类调用说明见 [USAGE.md](USAGE.md)，Agent 操作契约见 [AGENTS.md](AGENTS.md)。

最短流程：

```bash
BASE_DOMAIN=example.com scripts/ubuntu.sh check
sudo -E BASE_DOMAIN=example.com scripts/ubuntu.sh install
sudo docker compose build
sudo docker compose up -d
```

这四条命令不能替代 OAuth、飞书应用和 TLS 配置；继续按 `CONFIGURATION.md` 的编号完成。部署命令统一使用 `sudo`，因为 Compose 需要访问 Docker socket 和权限为 0600 的 `/etc` 环境文件。

## 固定边界

服务开放飞书业务命令、`api`、`schema`、`help` 和 `skills`，拒绝服务器认证、配置、profile、自动升级、本地文件、剪贴板、本地 Apps 工程和无界事件监听。高风险写操作保留 `lark-cli` 的退出码 10 门禁，服务不会自动追加 `--yes`。

更新检查在后台执行并缓存 6 小时。发现正式新版本时，工具响应会出现 `update_available`；业务输出和延迟不受检查失败影响，升级只能由管理员运行 `scripts/update-cli.sh`。

## 本地开发

```bash
UV_CACHE_DIR=/tmp/lark-cli-mcp-uv-cache uv sync --frozen
UV_CACHE_DIR=/tmp/lark-cli-mcp-uv-cache uv run python -m unittest discover -s tests -p 'test_*.py'
UV_CACHE_DIR=/tmp/lark-cli-mcp-uv-cache uv lock --check
```

启动需要完整 OAuth 环境变量和 Redis。普通开发优先运行单元测试；部署级验证按 `CONFIGURATION.md` 操作。
