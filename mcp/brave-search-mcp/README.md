# Brave Search MCP

把 [Brave Search CLI](https://github.com/brave/brave-search-cli) 的查询命令以一个 OAuth 保护的 Streamable HTTP MCP 工具暴露。服务端不解析、筛选或重写 `bx` 的 stdout/stderr。

> **域名配置**：本文档所有 `{your-domain}` 需替换为实际部署域名。运行 `scripts/ubuntu.sh` 前必须设置 `DOMAIN` 环境变量：`export DOMAIN=brave.your-domain.com`。

端点：`https://{your-domain}/mcp`（将 `{your-domain}` 替换为实际部署域名）。

## 快速部署

在 Ubuntu 服务器复制整个目录后，先执行环境检查；脚本不会生成或读取任何业务密钥。

```bash
cd brave-search-mcp
scripts/ubuntu.sh check
sudo scripts/ubuntu.sh install
sudo install -m 600 .env.example /etc/brave-search-mcp.env
sudoedit /etc/brave-search-mcp.env
sudo install -m 600 .redis.env.example /etc/brave-search-mcp.redis.env
sudoedit /etc/brave-search-mcp.redis.env
docker compose up -d --build
sudo install -m 644 deploy/brave-search-mcp.bootstrap.nginx.conf /etc/nginx/sites-available/brave-search-mcp
sudo ln -s /etc/nginx/sites-available/brave-search-mcp /etc/nginx/sites-enabled/brave-search-mcp
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d {your-domain}
sudo install -m 644 deploy/brave-search-mcp.nginx.conf /etc/nginx/sites-available/brave-search-mcp
sudo nginx -t && sudo systemctl reload nginx
```

完成后验证：

```bash
curl -i https://{your-domain}/mcp
curl -fsS https://{your-domain}/.well-known/oauth-protected-resource/mcp
curl -fsS https://{your-domain}/.well-known/oauth-authorization-server
```

未授权 `/mcp` 应返回 `401`。详细配置、OAuth、客户端连接、密钥轮换和故障处理见 [CONFIGURATION.md](CONFIGURATION.md)。

## 开发验证

```bash
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests
uv lock --check
BRAVE_MCP_ENV_FILE=.env.example BRAVE_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config
```

`bx` 固定为 1.5.0；Docker 构建下载官方 release 二进制并验证其 SHA-256。包装代码使用 MIT，`THIRD_PARTY_NOTICES.md` 记录上游 MPL-2.0 组件。
