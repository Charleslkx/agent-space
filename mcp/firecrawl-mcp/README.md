# Firecrawl CLI MCP

把 [Firecrawl CLI](https://github.com/firecrawl/cli) 的只读检索命令（`search`/`scrape`/`map`/`crawl`/`agent`/`research`/`credit-usage`）以一个 OAuth 保护的 Streamable HTTP MCP 工具暴露。服务端不解析、筛选或重写 CLI 的 stdout/stderr；`monitor`/`feedback`/`browser`/`download`/`parse`/`config` 等会写本地盘、留持久状态或管理凭据的命令一律拒绝，详见 [CONFIGURATION.md](CONFIGURATION.md)。

端点：`https://firecrawl.nexuszone.link/mcp`。

## 前置条件

- 一台 Ubuntu 22.04+ 服务器，有 root/sudo
- `firecrawl.nexuszone.link` 已解析到这台服务器
- 一个 [Firecrawl API Key](https://www.firecrawl.dev/app/api-keys)
- 一个专属的 GitHub OAuth App（不要和其他 MCP 共用，见下）

## 快速部署

```bash
cd firecrawl-mcp
scripts/ubuntu.sh check
sudo scripts/ubuntu.sh install
```

在 GitHub Developer Settings 建一个新 OAuth App：Homepage `https://firecrawl.nexuszone.link`，Authorization callback URL `https://firecrawl.nexuszone.link/auth/callback`。拿到 Client ID/Secret 后：

```bash
sudo install -m 600 .env.example /etc/firecrawl-mcp.env
openssl rand -hex 32      # -> FIRECRAWL_MCP_JWT_SIGNING_KEY
openssl rand -base64 32   # -> FIRECRAWL_MCP_STORAGE_KEY
openssl rand -hex 32      # -> FIRECRAWL_MCP_REDIS_PASSWORD（同一个值也写进下面的 redis env）
sudoedit /etc/firecrawl-mcp.env   # 填入上面三个密钥 + GitHub OAuth 的 client id/secret + FIRECRAWL_API_KEY
sudo install -m 600 .redis.env.example /etc/firecrawl-mcp.redis.env
sudoedit /etc/firecrawl-mcp.redis.env   # 填入与上面相同的 FIRECRAWL_MCP_REDIS_PASSWORD
docker compose up -d --build
sudo install -m 644 deploy/firecrawl-mcp.bootstrap.nginx.conf /etc/nginx/sites-available/firecrawl-mcp
sudo ln -s /etc/nginx/sites-available/firecrawl-mcp /etc/nginx/sites-enabled/firecrawl-mcp
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d firecrawl.nexuszone.link
sudo install -m 644 deploy/firecrawl-mcp.nginx.conf /etc/nginx/sites-available/firecrawl-mcp
sudo nginx -t && sudo systemctl reload nginx
```

完成后验证：

```bash
curl -i https://firecrawl.nexuszone.link/mcp
curl -fsS https://firecrawl.nexuszone.link/.well-known/oauth-protected-resource/mcp
curl -fsS https://firecrawl.nexuszone.link/.well-known/oauth-authorization-server
```

未授权 `/mcp` 应返回 `401`。

## 客户端接入

```bash
claude mcp add --transport http firecrawl https://firecrawl.nexuszone.link/mcp
```

Codex、ChatGPT、Claude.ai 的接入方式和给 agent 的传参/边界说明见 [AGENTS.md](AGENTS.md)；完整架构、环境变量、故障排查见 [CONFIGURATION.md](CONFIGURATION.md)。

## 日常运维

```bash
scripts/apikey.sh show                 # 查看当前 Firecrawl API Key（掩码）
scripts/apikey.sh set                  # 换一个 key（隐藏输入 + 校验 + 重建容器）
scripts/update-cli.sh --dry-run        # 查有没有新的 firecrawl CLI 版本
scripts/update-cli.sh                  # 升级到最新版本，失败自动回滚
docker compose logs -f mcp             # 看日志
```

## 开发验证

```bash
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests
uv lock --check
FIRECRAWL_MCP_ENV_FILE=.env.example FIRECRAWL_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config
docker build -t firecrawl-mcp-test . && docker run --rm firecrawl-mcp-test firecrawl --version
```

`firecrawl` CLI 固定版本写在 `Dockerfile` 的 `ARG FIRECRAWL_VERSION`（当前 1.19.27）；Docker 构建下载官方 release 二进制并按 `checksums.txt` 校验其 SHA-256，`scripts/update-cli.sh` 负责升级和失败回滚。包装代码使用 MIT，`THIRD_PARTY_NOTICES.md` 记录上游 ISC 组件。
