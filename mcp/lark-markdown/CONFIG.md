# Lark-Markdown MCP 配置

部署入口：[统一 Linux 部署运行手册](../DEPLOYMENT.md)。首次部署与升级以该手册为准；本文保留服务配置、工具边界和客户端参考。

可分发的 uv 项目：把 Markdown 内容写入飞书 Docx，支持并发批量拉取、并发批量推送、定点修改、创建文档、插入媒体及画板读写。公网部署后可由 ChatGPT、Claude.ai、Claude Desktop 和 Claude Code 通过 OAuth 2.1 连接。

> **域名配置**：本文档所有 `{your-domain}` 需替换为实际部署域名（如 `lark-markdown.example.com`）。

本文件提供本服务的配置和工具契约；Linux 安装与部署以统一运行手册为准。

## 1. 环境要求

本地开发支持 macOS／Linux，应用使用 Python 3.11+；准备脚本需要 Python 3.10+、venv／ensurepip，并自行管理隔离 uv，无需全局安装 uv。先按[统一部署预检](../DEPLOYMENT.md#21-python-与非默认工具预检)确认运行环境。服务器的 Python／uv 按运行手册安装，原生 `lark-cli` 首次取最新稳定 release，已安装后沿用现有版本，不依赖 Node.js 或部署用户的个人 PATH。CLI 必须支持 `config init --new`；升级前通过能力验收。

## 2. 飞书应用创建与绑定

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

服务不会自动升级；首次安装使用最新稳定 CLI，之后调试和修改使用当时已安装版本，明确升级时再更换并验证能力。CLI 在 JSON 中返回 `_notice.update` 时，`check_lark_cli` 会把它作为 `update_notice` 转交客户端；当前操作继续执行，由用户决定是否手动运行 `lark-cli update`。

## 3. 安装与测试

以下为本地开发测试，保留项目 `uv.lock`；服务器复制范围按统一部署手册执行：

```bash
cd mcp/lark-markdown
python3 ../scripts/prepare-python.py lark-markdown --native
deploy/python/.venv/bin/python -B -m unittest discover -s scripts -p 'test_*.py'
```

HTTP 传输在没有配置认证时拒绝启动。回环绑定不构成安全边界 —— 服务器部署正是 nginx 反代 `127.0.0.1:8765`，漏配 `LARK_MCP_AUTH_MODE` 就等于把可读写全部飞书文档的服务暴露到公网。本机跑一次冒烟测试可以显式豁免：

```bash
deploy/python/.venv/bin/python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765 --allow-unauthenticated
```

`--allow-unauthenticated` 仅限没有任何反向代理的开发机，绝不写进服务器的 systemd `ExecStart`。

## 4. Codex 安装

公网 OAuth 接入与验收按 [统一部署运行手册第 6 节](../DEPLOYMENT.md)执行。

## 5. 配置

| 配置 | 必需 | 说明 |
|-|-|-|
| `LARK_MCP_AUTH_MODE` | 公网必需 | ChatGPT/Claude 使用 `github`；支持自定义 Bearer 的客户端可使用 `token` |
| `LARK_MCP_BASE_URL` | OAuth 必需 | MCP 的公开 HTTPS origin，部署前将 `{your-domain}` 替换为实际域名 |
| `LARK_MCP_GITHUB_CLIENT_ID` | OAuth 必需 | GitHub OAuth App Client ID |
| `LARK_MCP_GITHUB_CLIENT_SECRET` | OAuth 必需 | GitHub OAuth App Client Secret |
| `LARK_MCP_GITHUB_USERS` | OAuth 必需 | 允许访问的 GitHub 登录名，逗号分隔且不区分大小写 |
| `LARK_MCP_GITHUB_USER` | 兼容旧配置 | 单一 GitHub 登录名；不得与 `LARK_MCP_GITHUB_USERS` 同时设置 |
| `LARK_MCP_JWT_SIGNING_KEY` | OAuth 必需 | 至少 32 字节的独立随机值，用于签发 MCP OAuth Token |
| `LARK_MCP_AUTH_TOKEN` | 与 Token 文件二选一 | 至少 32 字符；不适用于不接受自定义 Authorization header 的客户端 |
| `LARK_MCP_AUTH_TOKEN_FILE` | `token` 模式推荐 | 存放 Token 的普通文件；须由服务用户所有且权限为 `0600` 或更严格；不得与 `LARK_MCP_AUTH_TOKEN` 同时设置 |
| 工作目录 | 必需 | 服务固定在项目根目录创建 `.lark_publish/.run-*`；每次调用后删除 `.run-*`，`.lark_publish` 本身保留（并发下删空目录会与其他调用抢占） |
| TCP 8765 | 可改 | `--port` 设置；公网只开放反向代理的 443 |
| TLS 证书与私钥 | 直接公网模式必需 | 通过 `--tls-cert`、`--tls-key` 传入 |
| `--allow-unauthenticated` | 仅本地开发 | HTTP 传输未配置认证时默认拒绝启动；该开关是唯一的豁免，不要用于任何被反向代理的实例 |

固定边界：单批 100 项、每项正文 10 MiB、媒体 20 MiB、每次 `lark-cli` 60 秒；Nginx 另有限制单个请求体大小。批量请求还需按请求总大小拆分。

## 6. ChatGPT 与 Claude 远程部署

客户端不会从服务器“自动安装”MCP。它们连接公开 HTTPS `/mcp` 端点；OAuth 客户端可通过 CIMD 或 DCR 自动登记。当前实现使用 FastMCP 的 GitHub OAuth 代理提供 OAuth 2.1、PKCE、CIMD/DCR 和 protected-resource discovery，并只放行 `LARK_MCP_GITHUB_USERS`。

先在 GitHub 创建 OAuth App：

- Homepage URL：`https://{your-domain}`
- Authorization callback URL：`https://{your-domain}/auth/callback`

环境变量在服务进程启动前由 systemd 的 `EnvironmentFile` 加载；使用运行手册的 `.env.example`，首次创建且保留已有文件，不在普通 shell 中读取 root 所有的环境文件。

Ubuntu／Debian 的完整安装、服务账户、固定 PATH、目录权限、Nginx 模板、证书续期与升级回滚，统一执行 [部署运行手册](../DEPLOYMENT.md)。完整单元文件为 `deploy/lark-markdown-mcp.service`，代码路径为 `/opt/mcp/lark-markdown`，状态目录为 `/var/lib/lark-markdown`。

服务的 `NoNewPrivileges=true` 禁止通过 sudo 提权，默认部署下 `schedule_mcp_restart` 无法完成提权重启；管理员使用 `sudo systemctl restart lark-markdown-mcp`。其他文档操作不依赖该工具。服务运行时仅 `.lark_publish` 和状态目录可写，升级由管理员管理代码和虚拟环境。

在支持远程 MCP OAuth 的客户端创建连接器，填写 `https://{your-domain}/mcp` 并完成 GitHub 授权；客户端界面以当前版本为准。

Claude.ai、Claude Desktop、移动端和 Cowork 使用 OAuth 回调 `https://claude.ai/api/mcp/auth_callback`。在客户端的远程 MCP 设置中添加 URL：`https://{your-domain}/mcp`。

WorkBuddy 的 Custom MCP 使用固定回调 URI `workbuddy://workbuddy/mcp/custom-mcp%3Alark-markdown/oauth/callback`；服务已将该精确 URI 加入 OAuth 白名单。添加连接器后按 WorkBuddy 的 GitHub 授权流程完成登录。

Grok 自定义连接器使用固定回调 URI `https://grok.com/connectors-oauth-exchange-code/`；服务已将该精确 URI（包括末尾 `/`）加入 OAuth 白名单。

Claude Code 直接添加远程 HTTP MCP；它会发现 OAuth 并在 `/mcp` 中引导认证：

```bash
claude mcp add --transport http lark-markdown https://{your-domain}/mcp
```

Claude Code 使用动态 localhost 回调端口；服务器已允许 `localhost` 和 `127.0.0.1` 回环地址。

## 7. 直接 TLS 模式

默认使用 Nginx 终止 TLS。需要直接 TLS 时，服务支持 `--tls-cert`／`--tls-key`；`LARK_MCP_BASE_URL` 必须包含真实公开端口。管理员还需配置服务账户对证书的读取权限、证书续期和服务重启，不能直接复用仅 root 可读的私钥路径。

## 8. 静态 Token 模式

该模式用于 Codex 或支持自定义 Bearer Token 的 MCP 客户端；ChatGPT 和 Claude 托管端使用 OAuth：

密钥管理脚本故意拒绝非交互运行。以下命令必须由用户本人在真实终端执行；Agent 不得执行、代输确认语或读取输出：

```bash
sudo -u lark-markdown /opt/mcp/lark-markdown/deploy/python/.venv/bin/python \
  /opt/mcp/lark-markdown/scripts/manage_secret_key.py init /var/lib/lark-markdown/auth.token
# 轮换：把 init 改为 rotate
# 手动查看：把 init 改为 show
```

在 `/etc/lark-markdown.env` 中配置并重启 systemd 服务，单纯在终端 `export` 不会改变现有服务的环境：

```dotenv
LARK_MCP_AUTH_MODE=token
LARK_MCP_AUTH_TOKEN_FILE=/var/lib/lark-markdown/auth.token
```

客户端的 `LARK_MCP_AUTH_TOKEN` 环境变量应由操作者设为同一 token，再执行：

```bash
codex mcp add lark-markdown \
  --url https://{your-domain}/mcp \
  --bearer-token-env-var LARK_MCP_AUTH_TOKEN
```

若条目已存在，更新该条目的 URL／认证配置；不要删除其他连接。Token 本身不写入仓库配置或命令历史。

## 9. 验证

静态 Token 模式启动后可验证工具发现和认证：

```bash
deploy/python/.venv/bin/python scripts/test_https_mcp.py \
  --url https://{your-domain}/mcp \
  --token-env LARK_MCP_AUTH_TOKEN
```

自签名测试证书传 `--ca-cert /path/to/ca.crt`。未认证请求应返回 `401`；已认证的普通浏览器 GET 仍可能因 Accept 头不符合协议而返回 `406`。

`test_live_capabilities.py` 当前不携带认证，只适用于第 3 节显式启用无认证模式、且没有反向代理的本地开发实例。使用明确允许覆盖的测试 Docx：

```bash
deploy/python/.venv/bin/python scripts/test_live_capabilities.py \
  --url http://127.0.0.1:8765/mcp --doc "$TEST_DOC_URL"
```

## 10. MCP 工具

- `check_lark_cli`：检查 CLI、版本和用户登录态。
- `begin_lark_app_setup`、`complete_lark_app_setup`：经用户明确确认后创建并绑定新飞书应用；分步返回配置 URL/二维码并验收浏览器配置结果，不接收 App Secret。
- `schedule_mcp_restart`：在当前调用完成后延迟重启固定的 MCP 服务；必须传入确认词 `RESTART_LARK_MARKDOWN_MCP`，延迟范围为 5–300 秒。
- `begin_lark_auth`、`complete_lark_auth`：发起和完成飞书用户授权。
- `batch_pull`：批量读取 Markdown/XML 与 revision。普通正文使用 Markdown `simple`；当任务依赖 Markdown 无法可靠呈现的格式或原生结构时，使用 XML `full`，包括高亮、文字/背景颜色、下划线、Callout、分栏、引用、书签、URL 预览、按钮、提醒、画板，以及 block ID、样式属性和引用元数据。高亮在 XML 中表示为 `<span background-color="...">`，在 Markdown 中会降级为纯文本。
- `search_documents`：按关键词搜索 Docs、Wiki 和 Sheets，返回相关性排序的候选、摘要和资源标识；`count` 为 1–20。目标文档未知时先搜索，再定位正文。
- `find_document_text`：按精确文本返回有限上下文，不向模型返回全文；用于局部编辑前定位句子。
- `batch_push`：批量覆盖或追加 Markdown/XML；Markdown 中独立 `$$...$$` 公式会自动写为居中的原生公式段落，代码块中的字面量不转换。lark-cli 报告写入失败时立即报错，不中继为成功。
- `point_update`：仅在旧文本唯一命中时精确替换或删除；重复命中时先调用 `find_document_text` 缩小目标。lark-cli 报告写入失败时抛错，并附带失败后的文档状态（revision、替换文本是否已部分写入、原文尾部是否完整），用于区分"未生效"与"部分应用/截断"。
- `batch_point_update`：单篇文档内先预演全部唯一文本替换，再以 `revision_id` 串行写入；可传 `expected_revision_id` 拒绝过期定位。任一写入被 lark-cli 报告失败时立即停止（不继续写入可能已损坏的文档），并报告失败后的文档状态与已完成项。

> **写失败处理**：lark-cli 的写入接口可能以退出码 0 返回 `result:"failed"`（如 `str_replace` 未命中 pattern 时的降级路径），并可能对文档造成部分应用或截断。服务器对所有写操作校验该结果字段：任何显式失败都会以错误返回并附上失败后的文档状态。调用方收到写失败后必须先 `find_document_text` 核实，再决定是否重试——当状态显示写入已部分生效或文档尾部丢失时**不要重试**，否则会叠加损坏。
- `create_document`：在个人空间或 Drive 文件夹创建普通 Docx。
- `create_wiki_node`：在指定 Wiki 空间根目录或父节点下创建空白 Docx 页面。
- `create_wiki_space`：创建 Wiki 空间。
- `scan_document_assets`：在服务器端扫描飞书文档完整 XML，只返回图片和画板元数据、token 与数量。
- `insert_media`：从 base64 插入图片或附件并删除本地载荷。
- `whiteboard_query`、`whiteboard_update`：读取和更新飞书画板。

### 10.1. 创建 Wiki 的选择

| 目标 | 工具 | 必填参数 | 创建结果 |
|---|---|---|---|
| 新 Wiki 空间 | `create_wiki_space` | `name` | 一个独立 Wiki 空间，没有页面 |
| Wiki 空间根目录的页面 | `create_wiki_node` | `title`、`space_id` | 一个带空白 Docx 的 Wiki 节点 |
| 已有 Wiki 页面的子页面 | `create_wiki_node` | `title`、`parent_node_token` | 父节点下的一个带空白 Docx 的 Wiki 节点 |
| Drive 文件夹中的普通文档 | `create_document` | `content`，可选 `parent_token` | 一个普通 Docx，不创建 Wiki 节点 |

`parent_node_token` 是 Wiki 节点 token；`parent_token` 是 `create_document` 的 Drive 文件夹 token。两者不可混用。创建 Wiki 页面后，用返回的 Docx token/URL 调用 `batch_push` 写入 Markdown，并回读确认正文和层级。

错误为结构化 JSON，包含操作名；批量错误还包含失败索引、文档标识、已完成数量和底层 `cause`。临时载荷清理失败附加到原始错误，不覆盖真正失败原因。

## 11. 分发检查

```bash
rg -n '/Users/|\.agents/skills' . --hidden -g '!uv.lock'
bash ../scripts/check-python.sh --setup
python3 ../scripts/prepare-python.py lark-markdown --native
deploy/python/.venv/bin/python -B -m unittest discover -s scripts -p 'test_*.py'
```

如需额外审计依赖，先用 `bash ../scripts/check-python.sh --uv` 检查 uv，再在独立审计环境使用 pip-audit；不在运行环境安装审计工具或重解析依赖。

仓库不包含 Token、飞书凭据、证书、`.venv`、缓存或 `.lark_publish` 运行数据。
