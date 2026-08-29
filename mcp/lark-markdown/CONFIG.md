# Lark-Markdown MCP 配置

可分发的 uv 项目：把 Markdown 内容写入飞书 Docx，支持并发批量拉取、并发批量推送、定点修改、创建文档、插入媒体及画板读写。公网部署后可由 ChatGPT、Claude.ai、Claude Desktop 和 Claude Code 通过 OAuth 2.1 连接。

> **域名配置**：本文档所有 `{your-domain}` 需替换为实际部署域名（如 `lark-markdown.example.com`）。

`skills/lark-markdown/SKILL.md` 只说明如何使用已连接的远程 MCP；本文件集中说明 MCP 的安装、部署、认证和客户端连接配置。

## 环境要求

- macOS 或 Linux
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- `lark-cli`（不锁定版本，但必须支持 `config init --new`，以实际能力检查为准）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
npx @larksuite/cli@latest install
lark-cli --version
uv --version
```

## 飞书应用创建与绑定

每个独立部署必须先让服务器端 `lark-cli` 绑定一个飞书应用；该应用提供开放接口凭证与 scope，不是飞书智能体。开发机已有配置不会随项目分发，必须在目标服务器上以最终服务账户完成一次。

推荐通过 MCP 完成无密钥创建流程：

1. `check_lark_cli` 返回 `app_not_configured` 后，先征得用户同意创建应用。
2. 调用 `begin_lark_app_setup(confirmation="CREATE_LARK_APP")`。服务在后台运行 `lark-cli config init --new`，返回原始配置 URL 和 PNG 二维码；把两者交给用户，本轮不要等待。
3. 用户确认浏览器显示配置成功后调用 `complete_lark_app_setup`；只有返回 `status=configured` 才算绑定完成。
4. 再调用 `begin_lark_auth` / `complete_lark_auth`，完成 Docs、Drive、Wiki 用户授权。

`begin_lark_app_setup` 检测到已有配置时返回 `already_configured`，不会覆盖。它只支持独立 MCP 的新应用创建；`lark-cli config bind` 专用于 OpenClaw、Hermes、Lark Channel 等 Agent 工作区，不应拿来绑定本服务。若必须复用已有飞书应用，由管理员在服务器真实终端通过 `config init --app-id ... --app-secret-stdin` 配置，App Secret 不得作为 MCP 参数或写入命令历史。

完成应用绑定后再授权用户身份；文档、云空间和 Wiki 操作需要应用后台 scope 与用户授权同时具备：

```bash
lark-cli auth login --domain docs --domain drive --domain wiki --no-wait --json
lark-cli auth status --json --verify
```

授权命令返回验证链接时在浏览器完成授权。缺少 scope 时按 CLI 错误里的 `console_url` 在开发者后台开通，再重新执行最小范围授权。systemd 部署时，应用绑定和用户授权都必须以服务账户、下文同一组 `HOME`/XDG 路径完成；不要以部署账户配置后再切换服务账户。

服务不要求特定 `lark-cli` 版本，也不会自动升级。CLI 在 JSON 中返回 `_notice.update` 时，`check_lark_cli` 会把它作为 `update_notice` 转交客户端；当前操作继续执行，由用户决定是否手动运行 `lark-cli update`。

## 安装与测试

复制整个目录，必须保留 `uv.lock`：

```bash
cd mcp/lark-markdown
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s scripts -p 'test_*.py'
uv lock --check
```

HTTP 传输在没有配置认证时拒绝启动。回环绑定不构成安全边界 —— 服务器部署正是 nginx 反代 `127.0.0.1:8765`，漏配 `LARK_MCP_AUTH_MODE` 就等于把可读写全部飞书文档的服务暴露到公网。本机跑一次冒烟测试可以显式豁免：

```bash
uv run python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765 --allow-unauthenticated
```

`--allow-unauthenticated` 仅限没有任何反向代理的开发机，绝不写进服务器的 systemd `ExecStart`。

## Codex 安装

```bash
cd mcp/lark-markdown
uv sync --frozen
codex mcp remove lark-markdown 2>/dev/null || true
codex mcp add lark-markdown --url http://127.0.0.1:8765/mcp
codex mcp get lark-markdown
```

重启 Codex，使其重新加载 MCP 配置。

## 配置

| 配置 | 必需 | 说明 |
|-|-|-|
| `LARK_MCP_AUTH_MODE` | 公网必需 | ChatGPT/Claude 使用 `github`；支持自定义 Bearer 的客户端可使用 `token` |
| `LARK_MCP_BASE_URL` | OAuth 必需 | MCP 的公开 HTTPS origin，部署前将 `{your-domain}` 替换为实际域名 |
| `LARK_MCP_GITHUB_CLIENT_ID` | OAuth 必需 | GitHub OAuth App Client ID |
| `LARK_MCP_GITHUB_CLIENT_SECRET` | OAuth 必需 | GitHub OAuth App Client Secret |
| `LARK_MCP_GITHUB_USERS` | OAuth 必需 | 允许访问的 GitHub 登录名，逗号分隔且不区分大小写 |
| `LARK_MCP_GITHUB_USER` | 兼容旧配置 | 单一 GitHub 登录名；不得与 `LARK_MCP_GITHUB_USERS` 同时设置 |
| `LARK_MCP_JWT_SIGNING_KEY` | OAuth 必需 | 至少 32 字节的独立随机值，用于签发 MCP OAuth Token |
| `LARK_MCP_AUTH_TOKEN` | `token` 模式必需 | 至少 32 字符；不适用于不接受自定义 Authorization header 的客户端 |
| `LARK_MCP_AUTH_TOKEN_FILE` | `token` 模式推荐 | 存放 Token 的普通文件；须由服务用户所有且权限为 `0600` 或更严格；不得与 `LARK_MCP_AUTH_TOKEN` 同时设置 |
| 工作目录 | 必需 | 服务固定在项目根目录创建 `.lark_publish/.run-*`；每次调用后删除 `.run-*`，`.lark_publish` 本身保留（并发下删空目录会与其他调用抢占） |
| TCP 8765 | 可改 | `--port` 设置；公网只开放反向代理的 443 |
| TLS 证书与私钥 | 直接公网模式必需 | 通过 `--tls-cert`、`--tls-key` 传入 |
| `--allow-unauthenticated` | 仅本地开发 | HTTP 传输未配置认证时默认拒绝启动；该开关是唯一的豁免，不要用于任何被反向代理的实例 |

固定边界：单批 100 项、正文 10 MiB、媒体 20 MiB、每次 `lark-cli` 60 秒。更大的文件应先拆分；不要提高限制来绕过反向代理或飞书 API 的约束。

## ChatGPT 与 Claude 远程部署

客户端不会从服务器“自动安装”MCP。它们连接公开 HTTPS `/mcp` 端点；OAuth 客户端可通过 CIMD 或 DCR 自动登记。当前实现使用 FastMCP 的 GitHub OAuth 代理提供 OAuth 2.1、PKCE、CIMD/DCR 和 protected-resource discovery，并只放行 `LARK_MCP_GITHUB_USERS`。

先在 GitHub 创建 OAuth App：

- Homepage URL：`https://{your-domain}`
- Authorization callback URL：`https://{your-domain}/auth/callback`

创建仅服务账户可读的环境文件：

```bash
umask 077
sudo install -m 600 /dev/null /etc/lark-markdown.env
# 写入：
# LARK_MCP_AUTH_MODE=github
# LARK_MCP_BASE_URL=https://{your-domain}
# LARK_MCP_GITHUB_CLIENT_ID=...
# LARK_MCP_GITHUB_CLIENT_SECRET=...
# LARK_MCP_GITHUB_USERS=你的GitHub登录名,另一位允许访问的登录名
# LARK_MCP_JWT_SIGNING_KEY=<openssl rand -hex 32>
```

认证变量必须在进程启动前加载，因为 FastMCP 在导入时建立认证器：

```bash
set -a
. /etc/lark-markdown.env
set +a
uv run python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
```

Nginx 站点配置的关键项：

```nginx
# DCR 注册端点按协议不需要认证，而 FastMCP 存客户端注册时不设 TTL（它写的其他条目都设了）。
# 不限流的话，单台主机就能把 Redis 撑满，最终锁死白名单里的合法用户。合法客户端每次安装
# 只注册一次，这个额度足够宽松。
# map 的空值是关键：limit_req 的 key 为空时 nginx 不计数，因此其他路径完全不受影响，
# 也不必把 proxy 块复制成第二个 location。
# 变量名和 zone 名带服务前缀是必须的 —— 同机的 brave/firecrawl/lark 共用一个 nginx，
# zone 重名会让 `nginx -t` 直接失败，一次性拖垮该主机上所有站点。
map $request_uri $lark_register_key {
    default      "";
    ~^/register  $binary_remote_addr;
}
limit_req_zone $lark_register_key zone=lark_register:1m rate=10r/m;

server {
    listen 443 ssl http2;
    server_name {your-domain};
    ssl_certificate /etc/letsencrypt/live/{your-domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{your-domain}/privkey.pem;
    client_max_body_size 22m;

    # OAuth discovery、DCR、callback 与 /mcp 都必须转发。
    location / {
        limit_req zone=lark_register burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

用 systemd 时至少设置 `User`、`WorkingDirectory`、`EnvironmentFile`、自动重启和基础沙箱：

```ini
[Service]
User=lark-markdown
WorkingDirectory=/opt/lark-markdown
EnvironmentFile=/etc/lark-markdown.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=HOME=/var/lib/lark-markdown
Environment=XDG_CONFIG_HOME=/var/lib/lark-markdown/config
Environment=XDG_DATA_HOME=/var/lib/lark-markdown
StateDirectory=lark-markdown
ExecStart=/opt/lark-markdown/.venv/bin/python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=2
StartLimitIntervalSec=60
StartLimitBurst=3
NoNewPrivileges=true
PrivateTmp=true
```

`ExecStart` 里**不得**出现 `--allow-unauthenticated`。认证配置缺失时进程会拒绝启动，`StartLimitBurst` 让它在 3 次失败后停在 `failed` 而不是无限重启（systemd 默认是 10 秒内 5 次）。排查：

```bash
systemctl status lark-markdown-mcp.service
journalctl -u lark-markdown-mcp.service -n 30 --no-pager
```

看到 `HTTP transport requires configured authentication` 就是 `EnvironmentFile` 没被读到或 `LARK_MCP_AUTH_MODE` 拼错 —— 这是设计内的 fail-closed，修配置后 `systemctl reset-failed` 再启动。

`WorkingDirectory` 必须对服务账户可写：运行期载荷写在 `/opt/lark-markdown/.lark_publish/.run-*`，调用结束即删，父目录常驻。若后续加 `ProtectSystem=strict` 之类的加固，必须同时 `ReadWritePaths=/opt/lark-markdown/.lark_publish`。

`schedule_mcp_restart` 工具依赖服务账户能免密执行重启，否则该工具会失败（其他功能不受影响）：

```
lark-markdown ALL=(root) NOPASSWD: /usr/bin/systemctl restart lark-markdown-mcp.service
```

该服务账户必须单独完成一次 `lark-cli auth login`；凭据写入 `StateDirectory`，服务重启后继续使用。MCP 仅在冷启动、15 分钟缓存到期或调用方明确要求时执行 `auth status --verify`，不在每次文档操作前刷新授权。飞书撤销授权或刷新令牌失效时才需要再次授权。防火墙只开放 443，不开放 8765。

部署后确认 OAuth 发现文档可访问：

```bash
curl -fsS https://{your-domain}/.well-known/oauth-protected-resource/mcp
curl -fsS https://{your-domain}/.well-known/oauth-authorization-server
```

### 升级已部署的实例

服务无状态可重建：飞书凭据在 `StateDirectory`，认证配置在 `EnvironmentFile`，两者都不在代码目录里，所以升级只是替换代码目录再重启。在服务器上执行：

```bash
cd /opt/lark-markdown
sudo -u lark-markdown git pull            # 或 rsync -a --delete 排除 .venv/.lark_publish
sudo -u lark-markdown uv sync --frozen
sudo -u lark-markdown env PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s scripts -p 'test_*.py'
sudo systemctl restart lark-markdown-mcp.service
systemctl is-active lark-markdown-mcp.service
```

测试必须在服务器上跑一遍：其中的并发用例会在 `WorkingDirectory` 里做真实文件读写，能同时验证目录权限和临时载荷回收。测试全绿再重启。

重启后残留的 `.run-*` 由下一次启动清理（只清超过 `2 × CLI_TIMEOUT_SECONDS` 的目录，避免误删另一实例正在写的载荷），无需手工删除。

在 ChatGPT 的 Settings → Apps & Connectors → Advanced settings 启用 Developer mode，然后到 Settings → Connectors → Create，填写名称 `Lark-Markdown`、用途说明和 `https://{your-domain}/mcp`。ChatGPT 完成 OAuth 后会列出服务器注册的工具。服务器不能替用户把 connector 写入 ChatGPT 账户；面向其他用户公开分发时还需提交并发布 app 版本。

Claude.ai、Claude Desktop、移动端和 Cowork 使用 OAuth 回调 `https://claude.ai/api/mcp/auth_callback`。在 Settings → Connectors 添加远程 MCP URL：`https://{your-domain}/mcp`。

WorkBuddy 的 Custom MCP 使用固定回调 URI `workbuddy://workbuddy/mcp/custom-mcp%3Alark-markdown/oauth/callback`；服务已将该精确 URI 加入 OAuth 白名单。添加连接器后按 WorkBuddy 的 GitHub 授权流程完成登录。

Grok 自定义连接器使用固定回调 URI `https://grok.com/connectors-oauth-exchange-code/`；服务已将该精确 URI（包括末尾 `/`）加入 OAuth 白名单。

Claude Code 直接添加远程 HTTP MCP；它会发现 OAuth 并在 `/mcp` 中引导认证：

```bash
claude mcp add --transport http lark-markdown https://{your-domain}/mcp
```

Claude Code 使用动态 localhost 回调端口；服务器已允许 `localhost` 和 `127.0.0.1` 回环地址。

## 直接 TLS 模式

没有反向代理时可直接监听公网；缺少认证、证书或私钥时服务拒绝启动：

`LARK_MCP_BASE_URL` 必须与外部地址完全一致；下例监听 8765 时应设为 `https://{your-domain}:8765`。

```bash
set -a
. /etc/lark-markdown.env
set +a
uv run python scripts/mcp_server.py \
  --transport http --host 0.0.0.0 --port 8765 \
  --tls-cert /etc/letsencrypt/live/{your-domain}/fullchain.pem \
  --tls-key /etc/letsencrypt/live/{your-domain}/privkey.pem
```

## 静态 Token 模式

该模式用于 Codex 或支持自定义 Bearer Token 的 MCP 客户端；ChatGPT 和 Claude 托管端使用 OAuth：

密钥管理脚本故意拒绝非交互运行。以下命令必须由用户本人在真实终端执行；Agent 不得执行、代输确认语或读取输出：

```bash
sudo -u lark-markdown /opt/lark-markdown/.venv/bin/python \
  /opt/lark-markdown/scripts/manage_secret_key.py init /var/lib/lark-markdown/auth.token
# 轮换：把 init 改为 rotate
# 手动查看：把 init 改为 show
```

服务器环境使用文件路径，不把密钥复制到环境文件或命令历史：

```bash
export LARK_MCP_AUTH_MODE=token
export LARK_MCP_AUTH_TOKEN_FILE=/var/lib/lark-markdown/auth.token
```

旧的环境变量方式继续兼容：

```bash
export LARK_MCP_AUTH_MODE=token
export LARK_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
codex mcp remove lark-markdown 2>/dev/null || true
codex mcp add lark-markdown \
  --url https://{your-domain}/mcp \
  --bearer-token-env-var LARK_MCP_AUTH_TOKEN
```

## 验证

静态 Token 模式启动后可验证工具发现和认证：

```bash
uv run python scripts/test_https_mcp.py \
  --url https://{your-domain}/mcp \
  --token-env LARK_MCP_AUTH_TOKEN
```

自签名测试证书传 `--ca-cert /path/to/ca.crt`。未认证请求必须返回 `401`；MCP 原始 GET 可能返回 `406`，因为它不是浏览器页面。

使用明确允许覆盖的测试 Docx 验证 Markdown、原生块、定点修改和画板：

```bash
uv run python scripts/test_live_capabilities.py \
  --url http://127.0.0.1:8765/mcp --doc "$TEST_DOC_URL"
```

## MCP 工具

- `check_lark_cli`：检查 CLI、版本和用户登录态。
- `begin_lark_app_setup`、`complete_lark_app_setup`：经用户明确确认后创建并绑定新飞书应用；分步返回配置 URL/二维码并验收浏览器配置结果，不接收 App Secret。
- `schedule_mcp_restart`：在当前调用完成后延迟重启固定的 MCP 服务；必须传入确认词 `RESTART_LARK_MARKDOWN_MCP`，延迟范围为 5–300 秒。
- `begin_lark_auth`、`complete_lark_auth`：发起和完成飞书用户授权。
- `batch_pull`：批量读取 Markdown/XML 与 revision。普通正文使用 Markdown `simple`；当任务依赖 Markdown 无法可靠呈现的格式或原生结构时，使用 XML `full`，包括高亮、文字/背景颜色、下划线、Callout、分栏、引用、书签、URL 预览、按钮、提醒、画板，以及 block ID、样式属性和引用元数据。高亮在 XML 中表示为 `<span background-color="...">`，在 Markdown 中会降级为纯文本。
- `find_document_text`：按精确文本返回有限上下文，不向模型返回全文；用于局部编辑前定位句子。
- `batch_push`：批量覆盖或追加 Markdown/XML；Markdown 中独立 `$$...$$` 公式会自动写为居中的原生公式段落，代码块中的字面量不转换。lark-cli 报告写入失败时立即报错，不中继为成功。
- `point_update`：仅在旧文本唯一命中时精确替换或删除；重复命中时先调用 `find_document_text` 缩小目标。lark-cli 报告写入失败时抛错，并附带失败后的文档状态（revision、替换文本是否已部分写入、原文尾部是否完整），用于区分"未生效"与"部分应用/截断"。
- `batch_point_update`：单篇文档内先预演全部唯一文本替换，再以 `revision_id` 串行写入；可传 `expected_revision_id` 拒绝过期定位。任一写入被 lark-cli 报告失败时立即停止（不继续写入可能已损坏的文档），并报告失败后的文档状态与已完成项。

> **写失败安全（0.15.0）**：lark-cli 的写入接口可能以退出码 0 返回 `result:"failed"`（如 `str_replace` 未命中 pattern 时的降级路径），并可能对文档造成部分应用或截断。服务器对所有写操作校验该结果字段：任何显式失败都会以错误返回并附上失败后的文档状态。调用方收到写失败后必须先 `find_document_text` 核实，再决定是否重试——当状态显示写入已部分生效或文档尾部丢失时**不要重试**，否则会叠加损坏。
- `create_document`：在个人空间或 Drive 文件夹创建普通 Docx。
- `create_wiki_node`：在指定 Wiki 空间根目录或父节点下创建空白 Docx 页面。
- `create_wiki_space`：创建 Wiki 空间。
- `scan_document_assets`：在服务器端扫描飞书文档完整 XML，只返回图片和画板元数据、token 与数量。
- `insert_media`：从 base64 插入图片或附件并删除本地载荷。
- `whiteboard_query`、`whiteboard_update`：读取和更新飞书画板。

### 创建 Wiki 的选择

| 目标 | 工具 | 必填参数 | 创建结果 |
|---|---|---|---|
| 新 Wiki 空间 | `create_wiki_space` | `name` | 一个独立 Wiki 空间，没有页面 |
| Wiki 空间根目录的页面 | `create_wiki_node` | `title`、`space_id` | 一个带空白 Docx 的 Wiki 节点 |
| 已有 Wiki 页面的子页面 | `create_wiki_node` | `title`、`parent_node_token` | 父节点下的一个带空白 Docx 的 Wiki 节点 |
| Drive 文件夹中的普通文档 | `create_document` | `content`，可选 `parent_token` | 一个普通 Docx，不创建 Wiki 节点 |

`parent_node_token` 是 Wiki 节点 token；`parent_token` 是 `create_document` 的 Drive 文件夹 token。两者不可混用。创建 Wiki 页面后，用返回的 Docx token/URL 调用 `batch_push` 写入 Markdown，并回读确认正文和层级。

错误为结构化 JSON，包含操作名；批量错误还包含失败索引、文档标识、已完成数量和底层 `cause`。临时载荷清理失败附加到原始错误，不覆盖真正失败原因。

## 分发检查

```bash
rg -n '/Users/|\.agents/skills' . --hidden -g '!uv.lock'
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s scripts -p 'test_*.py'
uvx pip-audit --path .venv/lib/python*/site-packages --progress-spinner off
```

仓库不包含 Token、飞书凭据、证书、`.venv`、缓存或 `.lark_publish` 运行数据。
