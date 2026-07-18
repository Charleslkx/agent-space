# Lark Markdown MCP

可分发的 uv 项目：把 Markdown/Obsidian 内容写入飞书 Docx，支持批量拉取、批量推送、定点修改、创建文档、插入媒体及画板读写。

## 环境要求

- macOS 或 Linux
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- `lark-cli` 1.0.56（当前验证版本）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g @larksuite/cli@1.0.56
lark-cli --version
uv --version
```

完成飞书用户授权；文档、云空间和 Wiki 操作需要应用后台 scope 与用户授权同时具备：

```bash
lark-cli auth login --domain docs --domain drive --no-wait --json
lark-cli auth status --json --verify
```

授权命令返回验证链接时在浏览器完成授权。缺少 scope 时按 CLI 错误里的 `console_url` 在开发者后台开通，再重新执行最小范围授权。

## 安装与测试

复制整个目录，必须保留 `uv.lock`：

```bash
cd lark-markdown-mcp
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s scripts -p 'test_*.py'
uv lock --check
```

本地 HTTP 服务只监听回环地址，因此可以不设 MCP Token：

```bash
uv run python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
```

## Codex 安装

```bash
rsync -a --delete --exclude .venv --exclude .lark_publish \
  ./ ~/.codex/skills/lark-markdown-mcp/
cd ~/.codex/skills/lark-markdown-mcp
uv sync --frozen
codex mcp remove lark-markdown-mcp 2>/dev/null || true
codex mcp add lark-markdown-mcp --url http://127.0.0.1:8765/mcp
codex mcp get lark-markdown-mcp
```

重启 Codex，使其重新加载 `SKILL.md`。

## 配置

| 配置 | 必需 | 说明 |
|-|-|-|
| `LARK_MCP_AUTH_TOKEN` | 公网必需 | 至少 32 字符；只从环境变量读取 |
| 工作目录 | 必需 | 服务在这里创建 `.lark_publish/.run-*`，每次调用后删除 |
| TCP 8765 | 可改 | `--port` 设置；公网只开放反向代理的 443 |
| TLS 证书与私钥 | 直接公网模式必需 | 通过 `--tls-cert`、`--tls-key` 传入 |

固定边界：单批 100 项、正文 10 MiB、媒体 20 MiB、每次 `lark-cli` 60 秒。更大的文件应先拆分；不要提高限制来绕过反向代理或飞书 API 的约束。

## 推荐的公网部署

生产环境使用 Nginx/Caddy 终止 HTTPS，MCP 仅监听同机 `127.0.0.1`。先创建只允许服务账户读取的环境文件：

```bash
umask 077
openssl rand -hex 32 > /tmp/lark-mcp-token
# 将下列内容写入服务环境文件，不要提交到仓库：
# LARK_MCP_AUTH_TOKEN=<上一步生成的值>
```

服务进程必须在启动前获得 `LARK_MCP_AUTH_TOKEN`，因为 FastMCP 在导入时建立鉴权器：

```bash
export LARK_MCP_AUTH_TOKEN='至少32字符的随机值'
uv run python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
```

Nginx 站点配置的关键项：

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;
    ssl_certificate /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;
    client_max_body_size 22m;

    location /mcp {
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
User=lark-mcp
WorkingDirectory=/opt/lark-markdown-mcp
EnvironmentFile=/etc/lark-markdown-mcp.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/lark-markdown-mcp/.venv/bin/python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
```

该服务账户必须单独完成 `lark-cli auth login`；复制项目不会复制飞书凭据。防火墙只开放 443，不开放 8765。

## 直接 TLS 模式

没有反向代理时可直接监听公网；缺少 Token、证书或私钥时服务拒绝启动：

```bash
export LARK_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
uv run python scripts/mcp_server.py \
  --transport http --host 0.0.0.0 --port 8765 \
  --tls-cert /etc/letsencrypt/live/mcp.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/mcp.example.com/privkey.pem
```

Codex 只保存 Token 环境变量名：

```bash
export LARK_MCP_AUTH_TOKEN='与服务器一致的值'
codex mcp remove lark-markdown-mcp 2>/dev/null || true
codex mcp add lark-markdown-mcp \
  --url https://mcp.example.com/mcp \
  --bearer-token-env-var LARK_MCP_AUTH_TOKEN
```

## 验证

本地或公网启动后，先验证工具发现和认证：

```bash
uv run python scripts/test_https_mcp.py \
  --url https://mcp.example.com/mcp \
  --token-env LARK_MCP_AUTH_TOKEN
```

自签名测试证书传 `--ca-cert /path/to/ca.crt`。未认证请求必须返回 `401`；MCP 原始 GET 可能返回 `406`，因为它不是浏览器页面。

使用明确允许覆盖的测试 Docx 验证 Markdown、原生块、定点修改和画板：

```bash
uv run python scripts/test_live_capabilities.py \
  --url http://127.0.0.1:8765/mcp --doc "$TEST_DOC_URL"
uv run python scripts/test_live_media.py --doc "$TEST_DOC_URL"
```

## MCP 工具

- `check_lark_cli`：检查 CLI、版本和用户登录态。
- `batch_pull`：批量读取 Markdown/XML 与 revision。
- `batch_push`：批量覆盖或追加 Markdown/XML。
- `point_update`：精确替换或删除匹配内容。
- `create_document`：在个人空间、Drive 文件夹或 Wiki 节点创建 Docx。
- `insert_media`：从 base64 插入图片或附件并删除本地载荷。
- `whiteboard_query`、`whiteboard_update`：读取和更新飞书画板。

错误为 JSON 文本，包含操作名；批量错误还包含失败索引、文档标识和已完成数量。临时载荷清理失败会直接报错。

## 分发检查

```bash
rg -n 'lark-obsidian[-]publish|/Users/|\.agents/skills' . --hidden -g '!uv.lock'
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s scripts -p 'test_*.py'
uvx pip-audit --path .venv/lib/python*/site-packages --progress-spinner off
```

仓库不包含 Token、飞书凭据、证书、`.venv`、缓存或 `.lark_publish` 运行数据。
