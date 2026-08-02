# 配置与部署

## 1. 架构与前提

请求路径为：MCP 客户端 → Nginx/TLS → FastMCP → GitHub OAuth 白名单 → `lark-cli` → 飞书 OpenAPI。Redis 只保存加密的 MCP OAuth 状态；`lark-state` 卷保存共享飞书配置和用户 refresh token。

服务器要求 Ubuntu x86_64 或 arm64、至少 1 GiB 内存和 2 GiB 可用磁盘。DNS 必须预先把 `lark.{基础域名}` 指向服务器。用户只提供基础域名，所有脚本和示例固定添加 `lark.` 前缀。

```bash
BASE_DOMAIN=example.com scripts/ubuntu.sh check
sudo -E BASE_DOMAIN=example.com scripts/ubuntu.sh install
```

脚本拒绝协议、路径、端口和已经带 `lark.` 的值。环境检查不会修改系统；`install` 只安装 Docker Compose、Nginx 和 Certbot。
安装脚本验证 root 可以连接 Docker daemon。后续 Docker 和 Compose 命令统一使用 `sudo`；这同时满足 Docker socket 与 0600 环境文件的权限要求，不需要把部署用户加入等同 root 权限的 `docker` 组。

## 2. GitHub OAuth App

在 GitHub Developer Settings 创建独立 OAuth App：

| 字段 | 值 |
|---|---|
| Application name | Lark CLI MCP |
| Homepage URL | `https://lark.example.com` |
| Authorization callback URL | `https://lark.example.com/auth/callback` |

不能复用其他 MCP 的 OAuth App，因为 GitHub OAuth App 只有一个 callback。服务只申请 `read:user`，登录后再按 `LARK_CLI_MCP_GITHUB_USERS` 检查 GitHub login；这里填写登录名，不填显示名或邮箱。

## 3. 环境文件

生成密钥：

```bash
openssl rand -hex 32       # JWT signing key
openssl rand -base64 32    # Fernet storage key
openssl rand -hex 32       # Redis password
```

创建 `/etc/lark-cli-mcp.env`，权限 0600：

```dotenv
LARK_CLI_MCP_BASE_URL=https://lark.example.com
LARK_CLI_MCP_GITHUB_CLIENT_ID=GitHub OAuth App Client ID
LARK_CLI_MCP_GITHUB_CLIENT_SECRET=GitHub OAuth App Client Secret
LARK_CLI_MCP_GITHUB_USERS=alice,bob
LARK_CLI_MCP_JWT_SIGNING_KEY=64位十六进制值
LARK_CLI_MCP_STORAGE_KEY=Fernet URL-safe base64值
LARK_CLI_MCP_REDIS_PASSWORD=Redis密码
LARK_CLI_MCP_UPDATE_CHECK=1
# 可选：同时运行的 lark-cli 子进程上限，默认 8
# LARK_CLI_MCP_MAX_CONCURRENCY=8
```

`LARK_CLI_MCP_MAX_CONCURRENCY` 限制的是子进程数而不是请求数：FastMCP 的工作线程池仍会接纳 40 个并发调用，超出上限的在服务端排队最多 5 秒，之后返回 `server is at capacity`（该错误表示本次没有执行任何请求，可安全重试）。因为所有 lark-cli 进程共用 `lark-state` 卷里的同一份凭据存储，这个上限同时也限制了并发触碰凭据的进程数。要调大必须同步调大 compose 的 `mem_limit` 和 `pids_limit`。

`lark-cli` 不在 PATH、或 `LARK_CLI_MCP_STATE_DIR` 不存在/不可读写时，进程**启动即失败**并打印原因，不会带着一个每次调用都失败的服务通过健康检查（健康检查只探测端口）。

创建 `/etc/lark-cli-mcp.redis.env`，权限 0600，只包含同一份 Redis 密码：

```dotenv
LARK_CLI_MCP_REDIS_PASSWORD=Redis密码
```

```bash
sudo chmod 600 /etc/lark-cli-mcp.env /etc/lark-cli-mcp.redis.env
```

JWT 或 Fernet 密钥轮换会让全部 MCP 会话失效。恢复 Redis 备份时必须同时恢复原 Fernet 密钥。Redis 容器不能获得 GitHub 或飞书凭据。

## 4. 构建并启动

在本目录执行：

```bash
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

MCP 只监听宿主 `127.0.0.1:8768`，Redis没有宿主端口。`state-init` 和 `redis-init` 只修复命名卷权限，完成后正常退出。

## 5. 配置飞书应用与共享身份

以下命令必须由管理员在真实终端执行。它们使用与 MCP 相同的 `lark-state` 卷：

```bash
sudo docker compose exec mcp lark-cli config init --new
sudo docker compose exec mcp lark-cli auth login --domain all --no-wait --json
```

第二条命令返回 `verification_url` 和 `device_code`。原样打开 URL 完成飞书授权，再执行：

```bash
sudo docker compose exec mcp lark-cli auth login --device-code '<device_code>'
sudo docker compose exec mcp lark-cli auth status --json --verify
```

不得把 URL 或 device code 写入配置文件。若返回权限错误，按错误中的 `console_url` 在飞书开发者后台给应用开通缺失 scope，再重新执行最小范围或 `--domain all` 授权。bot 身份只依赖应用后台 scope；用户身份同时要求后台 scope 和用户授权。

重建容器不会删除 `lark-state`。删除该卷会清除飞书配置和用户登录态。

## 6. Nginx 与 TLS

把模板中的 `YOUR_BASE_DOMAIN` 替换为基础域名；保留 `lark.` 前缀：

```bash
sudo cp deploy/lark-mcp.bootstrap.nginx.conf /etc/nginx/sites-available/lark-cli-mcp
sudo sed -i 's/YOUR_BASE_DOMAIN/example.com/g' /etc/nginx/sites-available/lark-cli-mcp
sudo ln -s /etc/nginx/sites-available/lark-cli-mcp /etc/nginx/sites-enabled/lark-cli-mcp
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d lark.example.com
```

证书签发后用 `deploy/lark-mcp.nginx.conf` 替换站点文件、再次替换域名、运行 `nginx -t` 并 reload。Nginx 必须代理所有路径，不能只代理 `/mcp`，否则 OAuth discovery、注册或 callback 会失败。

验证：

```bash
curl -fsS https://lark.example.com/.well-known/oauth-protected-resource/mcp
curl -fsS https://lark.example.com/.well-known/oauth-authorization-server
curl -i https://lark.example.com/mcp
```

最后一条未认证请求应返回 401；这表示服务可达且正在要求 OAuth。

## 7. 客户端连接

Codex：

```bash
codex mcp add lark-cli --url https://lark.example.com/mcp
codex mcp login lark-cli
```

Claude Code：

```bash
claude mcp add --transport http lark-cli https://lark.example.com/mcp
```

OpenCode 的 `opencode.json`：

```json
{
  "mcp": {
    "lark-cli": {
      "type": "remote",
      "url": "https://lark.example.com/mcp",
      "enabled": true
    }
  }
}
```

然后运行 `opencode mcp auth lark-cli`。ChatGPT、Claude.ai 和 Claude Desktop 在远程 Connector 界面添加同一 URL；不要把公网 HTTP MCP 填入只接受本地 stdio 命令的配置项。

## 8. 更新、备份和恢复

工具响应中的 `update_available` 只表示官方发布了较新的稳定版。检查或升级：

```bash
sudo scripts/update-cli.sh --dry-run
sudo scripts/update-cli.sh
sudo scripts/update-cli.sh 1.0.82
```

升级脚本核对 amd64、arm64 和 checksums 资产，修改固定版本，重建并等待健康检查，再验证容器内版本；失败时恢复旧版本。服务不会自行运行此脚本。设置 `LARK_CLI_MCP_UPDATE_CHECK=0` 并重建容器可关闭后台检查。

备份 `lark-state`、`redis-data` 和两个 `/etc` 环境文件。环境文件必须单独加密保存。恢复时先恢复环境文件和卷，再启动 Compose；Fernet 密钥与 Redis 内容不匹配时，旧 OAuth 状态无法解密，客户端必须重新登录。

## 9. 故障定位

| 现象 | 检查 |
|---|---|
| DNS 或证书失败 | `lark.example.com` 解析、Certbot 证书、Nginx `server_name` |
| `/mcp` 401 | 正常 OAuth 起点；检查 discovery 文档 |
| OAuth callback 404 | GitHub callback、`BASE_URL`、Nginx 全路径代理必须一致 |
| GitHub 登录后没有工具 | 检查白名单使用 login 且大小写无关 |
| 重启后重新 OAuth | Redis 卷、JWT/Fernet 密钥是否变化 |
| 飞书用户未登录 | `sudo docker compose exec mcp lark-cli auth status --json --verify` |
| 飞书 403/scope 错误 | 根据身份检查后台 bot scope 或用户授权，使用错误中的 `console_url` |
| 退出码 10 | 高风险写操作等待用户确认；确认后原 argv 末尾追加 `--yes` |
| 502 | `sudo docker compose ps`、`sudo docker compose logs mcp`、Nginx error log、宿主 8768 |
| 容器反复重启，日志第一条是 `lark-cli is not installed` 或 `must exist and be readable/writable` | 启动前置检查失败。补齐镜像里的 lark-cli，或检查 `lark-state` 卷的属主（`state-init` 应已 chown 到 10001） |
| `server is at capacity` | 并发子进程已满（`LARK_CLI_MCP_MAX_CONCURRENCY`，默认 8）。**本次没有执行任何请求**，直接重试 |
| 参数被拒且提示 `args[0]` / `args[1]` | 命令和子命令必须分别是 args[0]、args[1]，标志不能前置。这不是风格要求：标志的值会顶替命令位置，让校验器和 Cobra 对「哪个 token 是命令」产生分歧 |
| 首次连接时 `/register` 返回 429 | nginx 对 DCR 注册端点限流（每 IP 10 次/分，可突发 6 次）。正常客户端每次安装只注册一次，等一分钟重试 |
| `nginx: [emerg] limit_req_zone "..." is already bound` | 同机多个 MCP 用了相同 zone 名。本服务用 `lark_cli_register`，注意不要和 lark-markdown 的 `lark_register` 重名 |
| `update_available` 长时间不出现 | 检查开关、GitHub 出网和缓存周期；不影响业务调用 |

部署完成后按 [USAGE.md](USAGE.md) 验证两个工具。
