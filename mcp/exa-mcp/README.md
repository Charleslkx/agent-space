# Exa MCP

自托管的 [Exa 官方 MCP](https://exa.ai/docs/reference/exa-mcp) 透传服务。工具、参数、返回值全部由 `https://mcp.exa.ai/mcp` 在请求时实时下发并原样转发，本地不重新声明任何一个工具 —— 所以用法与官方 MCP 完全一致，Exa 改 API 时本服务无需改代码。

和其他平台自带的 Exa connector 的区别只有一点：**API Key 配在服务端**，客户端不需要也不能传 key，因此不会撞匿名免费额度的 tier 墙。

> **域名配置**：本文档所有 `{your-domain}` 需替换为实际部署域名。运行 `scripts/ubuntu.sh` 前必须设置 `DOMAIN` 环境变量：`export DOMAIN=exa.your-domain.com`。

端点：`https://{your-domain}/mcp`。

默认暴露 Exa 全部四个工具：`web_search_exa`、`web_fetch_exa`、`agent_run`、`web_search_advanced_exa`。

## 前置条件

- 一台 Ubuntu 22.04+ 服务器，有 root/sudo
- `{your-domain}` 已解析到这台服务器
- 一个 [Exa API Key](https://dashboard.exa.ai/api-keys)
- 一个专属的 GitHub OAuth App（不要和其他 MCP 共用，见下）

## 快速部署

```bash
cd exa-mcp
export DOMAIN=exa.your-domain.com
scripts/ubuntu.sh check
sudo scripts/ubuntu.sh install
```

如果这台机器已经在跑 brave/firecrawl/lark 中的任何一个,那 Docker、nginx、certbot 都已就位,`check` 会打印一行 `note: TCP 80 is served by nginx; ...` 然后正常通过,`install` 也可以直接跳过 —— 本服务只是往现有 nginx 里加一个新的 server block。预检唯一会硬性拒绝的是宿主 8769 被占用,或 80/443 被**非 nginx** 的程序占着。

部署完成后建议按 [CONFIGURATION.md](CONFIGURATION.md) §6 的表逐项确认没和现有三个服务撞端口、撞 zone 名、撞 OAuth App。

在 GitHub Developer Settings 建一个新 OAuth App：Homepage `https://{your-domain}`，Authorization callback URL `https://{your-domain}/auth/callback`。拿到 Client ID/Secret 后：

```bash
sudo install -m 600 .env.example /etc/exa-mcp.env
openssl rand -hex 32      # -> EXA_MCP_JWT_SIGNING_KEY
openssl rand -base64 32   # -> EXA_MCP_STORAGE_KEY
openssl rand -hex 32      # -> EXA_MCP_REDIS_PASSWORD（同一个值也写进下面的 redis env）
sudoedit /etc/exa-mcp.env   # 填入上面三个密钥 + GitHub OAuth 的 client id/secret + EXA_API_KEY
sudo install -m 600 .redis.env.example /etc/exa-mcp.redis.env
sudoedit /etc/exa-mcp.redis.env   # 填入与上面相同的 EXA_MCP_REDIS_PASSWORD
docker compose up -d --build
sudo install -m 644 deploy/exa-mcp.bootstrap.nginx.conf /etc/nginx/sites-available/exa-mcp
sudo ln -s /etc/nginx/sites-available/exa-mcp /etc/nginx/sites-enabled/exa-mcp
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d {your-domain}
sudo install -m 644 deploy/exa-mcp.nginx.conf /etc/nginx/sites-available/exa-mcp
sudo nginx -t && sudo systemctl reload nginx
```

完成后验证：

```bash
curl -i https://{your-domain}/mcp
curl -fsS https://{your-domain}/.well-known/oauth-protected-resource/mcp
curl -fsS https://{your-domain}/.well-known/oauth-authorization-server
```

未授权 `/mcp` 应返回 `401`。启动日志里会打印实际镜像到的上游工具清单：

```bash
docker compose logs mcp | grep 'proxying'
# proxying 4 upstream Exa tools: agent_run, web_fetch_exa, web_search_advanced_exa, web_search_exa
```

校验 API Key 是否真的有效要用下面这条 —— 启动探测只能确认上游可达，**不能**判断 key 好坏（Exa 的 MCP 网关对任何格式正确的 key 都会正常返回 `tools/list`，只在真正调用工具时才鉴权）：

```bash
sudo scripts/apikey.sh verify
```

## 客户端接入

支持 OAuth 的客户端直接填 URL，首次连接会拉起浏览器走 GitHub 登录：

```bash
claude mcp add --transport http exa https://{your-domain}/mcp
```

```json
{ "mcpServers": { "exa": { "url": "https://{your-domain}/mcp" } } }
```

已内置回调地址的客户端：ChatGPT / Claude（Web、Desktop、Code）/ Grok / Cursor / WorkBuddy / Codex / VS Code / Zed / Gemini CLI。

**Trae 例外**：Trae 的 MCP 客户端[不支持 OAuth 2.0 交互式登录，也不支持刷新 token](https://forum.trae.cn/t/topic/175024)，只支持静态 header。给它签一个长期 token：

```bash
sudo scripts/apikey.sh token add your-github-login
```

命令会打印一次 token（之后不再可见），填进 Trae 的 `mcp.json`：

```json
{
  "mcpServers": {
    "exa": {
      "url": "https://{your-domain}/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

静态 token 与 OAuth 走同一份 `EXA_MCP_GITHUB_USERS` 白名单授权，权限完全等价。各客户端的详细配置、给 agent 的用法说明见 [AGENTS.md](AGENTS.md)；完整架构、环境变量、故障排查见 [CONFIGURATION.md](CONFIGURATION.md)。

## 日常运维

```bash
scripts/apikey.sh show                 # 查看当前 Exa API Key（掩码）
scripts/apikey.sh verify               # 校验当前 key 是否还有效
scripts/apikey.sh set                  # 换一个 key（隐藏输入 + 校验 + 重建容器）
scripts/apikey.sh token list           # 查看已签发的静态 token（掩码）
scripts/apikey.sh token add LOGIN      # 签发/轮换某个 login 的静态 token
scripts/apikey.sh token delete LOGIN   # 吊销静态 token
docker compose logs -f mcp             # 看日志
```

没有 `update-cli.sh`：这里没有需要 pin 版本的 CLI 二进制，上游工具集由 Exa 自己维护，本服务自动跟随。

## 开发验证

```bash
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests
uv lock --check
EXA_MCP_ENV_FILE=.env.example EXA_MCP_REDIS_ENV_FILE=.redis.env.example docker compose config
docker build -t exa-mcp-test .
```

测试全部离线运行：代理镜像行为用一个内存里的 FastMCP 假上游验证，不需要真的 Exa key、Redis 或 GitHub OAuth App。包装代码使用 MIT，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
