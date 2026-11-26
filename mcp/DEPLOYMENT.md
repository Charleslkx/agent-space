# MCP Linux 部署运行手册

版本策略按执行时点生效：未来首次配置时实时查询官方当时的最新稳定版，不以本文编写日期或仓库开发锁文件中的版本作为安装默认值。已有安装保留当前版本，明确升级时才重新查询。

本文件是六个服务的统一部署入口；从任意服务的 README 或配置文档进入后，按本文件顺序执行。配置文档保留各服务的环境变量、工具边界和认证细节。默认架构采用独立域名、Nginx、Let's Encrypt、GitHub OAuth；五个服务采用 Docker Compose，Lark Doc 采用 systemd。部署工作目录固定为 `/opt/mcp/<服务名>`。所有示例域名、凭据和用户标识均为占位值；真实配置只在目标服务器的私有环境文件中填写，不回写源码或部署记录。

## 1. 支持范围与部署输入

| 服务目录／`SERVICE` | 示例 `DOMAIN` | 本机端口 | 环境变量前缀 | Nginx 限流区 | 运行方式 |
|---|---|---|---|---|---|
| `brave-search-mcp` | `brave.example.com` | 8766 | `BRAVE_MCP` | `brave_register` | Compose |
| `firecrawl-mcp` | `firecrawl.example.com` | 8767 | `FIRECRAWL_MCP` | `firecrawl_register` | Compose |
| `exa-mcp` | `exa.example.com` | 8769 | `EXA_MCP` | `exa_register` | Compose |
| `lark-cli-mcp` | `lark.example.com` | 8768 | `LARK_CLI_MCP` | `lark_cli_register` | Compose |
| `colab-cli-mcp` | `colab.example.com` | 8770 | `COLAB_MCP` | `colab_register` | Compose |
| `lark-doc` | `lark-doc.example.com` | 8765 | `LARK_MCP` | `lark_doc_register` | systemd |

- 系统：Ubuntu 22.04、24.04、26.04、26.10，Debian 12、13；systemd；amd64 或 arm64。每个实例至少预留 1 GiB 内存、2 GiB 磁盘，多个实例需累加并预留构建空间。
- 必填输入：服务名、小写公开域名、独立 GitHub OAuth App 的 Client ID／Secret、允许访问的 GitHub login，以及 Brave／Firecrawl／Exa 对应的 API Key。飞书服务还需要交互式完成应用绑定和用户授权。Colab 需要在目标服务器准备专用 Google 用户的 ADC 文件。
- DNS：域名的全部 A／AAAA 记录指向当前服务器；没有 IPv6 服务时删除 AAAA。开放入站 TCP 80、443，保留管理员 SSH 入口；不开放 8765–8770 和 Redis。出站需访问软件源、GitHub、容器仓库及上游 API。
- 同机服务共用 Nginx；已有 Caddy／Apache 占用 80／443 时，先选择并配置唯一入口。本手册的 Nginx 安装流程不能直接用于该场景。已有 Lark Caddy 辅助脚本属于独立部署方式。
- Agent 缺少以上输入时报告缺项；已有实例先检查环境文件和运行状态，禁止用示例覆盖实际配置。GitHub、飞书和 Google 浏览器授权由用户完成。

## 2. 准备代码与宿主机

以下命令每块逐条执行，任意一步失败即停止并修复；不要把失败后的后续命令继续粘贴执行。

使用第 9.1 节生成的已审核分发包。先在目标服务器验证随包提供的 SHA-256 校验文件。首次安装时将整个 `mcp/` 解到 `/opt`，部署路径为 `/opt/mcp/`。已有实例先解到临时目录，再按第 7 节备份并同步代码。保留 `/etc`、`/var/lib`、Compose 持久卷以及本机的 `deploy/*.version`、`deploy/python/` 和 `python/`。部署示例在同一 Bash 会话中执行。

```bash
# 用表中当前要部署的服务和实际域名替换这两个值。
SERVICE=brave-search-mcp
DOMAIN=brave.example.com
cd "/opt/mcp/$SERVICE"
sudo apt-get update
sudo apt-get install -y ca-certificates curl openssl iproute2
sudo env DOMAIN="$DOMAIN" bash scripts/ubuntu.sh install
sudo env DOMAIN="$DOMAIN" bash scripts/ubuntu.sh check
```

`ubuntu.sh` 是兼容入口，Ubuntu／Debian 共用 `../scripts/linux.sh`；Lark 也接受旧的 `BASE_DOMAIN` 输入，统一流程使用完整 `DOMAIN`。五个 Compose 服务的安装器使用 Docker 官方 apt 源，验证 Compose 插件和 daemon，不自动移除已有容器运行时。已具备依赖的服务器只运行 `check`；它会报告已占用的应用端口，管理员需确认是否属于当前实例。安装模式拒绝占用的应用端口。

DNS 预检只验证有解析结果，公网可达性必须从服务器外验证。`install` 首次会安装并启动所需的 Docker／Nginx；其他站点配置保持各自管理。

### 2.1. Python 与非默认工具预检

在写入凭据、版本记录或服务配置前执行；使用 sudo 的部署也必须在 sudo 环境下通过，不能只依赖交互终端的 PATH：

```bash
sudo bash ../scripts/check-python.sh --setup
```

宿主机准备脚本需要 Python 3.10+、`ssl`／`json`／`urllib`，并实际创建临时虚拟环境确认 venv、ensurepip 和环境内 pip 可用；不要求系统 Python 安装 pip。缺失时先修复依赖再继续：Ubuntu／Debian 安装 `python3 python3-venv`，自选解释器使用对应的 `python3.X-venv`；macOS 使用包含 venv／ensurepip 的 Python 发行版。

MCP 应用使用兼容的 Python 3.12；`prepare-python.py` 在隔离工具环境安装 uv、检查其退出状态，并记录实际版本，无需预先安装全局 uv。已有依赖记录直接复用；需要修复原生虚拟环境时，先通过同一预检，再安装记录中的 uv 版本。失败时不写入或替换应用依赖记录。首次配置按执行时点选择官方最新稳定工具／应用依赖，调试不触发升级。

确需自行运行 `uv`／`uvx` 命令时，先执行 `bash ../scripts/check-python.sh --uv`，再按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)补齐缺项，并检查该执行账户的 PATH；已有 uv 不因环境预检被升级。缺失 Docker、CLI 或 SDK 的检查见各步骤，Python 预检不替代这些检查。

## 3. 配置认证与环境文件

每个服务建立独立的 [GitHub OAuth App](https://github.com/settings/developers)：

| GitHub 字段 | 值 |
|---|---|
| Homepage URL | `https://实际DOMAIN` |
| Authorization callback URL | `https://实际DOMAIN/auth/callback` |

首次部署复制示例；命令通过 `test` 防止覆盖已有环境文件。Lark Doc 的环境文件名同样为 `/etc/lark-doc.env`。

```bash
sudo test -e "/etc/$SERVICE.env" || sudo install -o root -g root -m 600 .env.example "/etc/$SERVICE.env"
sudoedit "/etc/$SERVICE.env"
```

根据当前服务的变量前缀填写：`BASE_URL=https://实际DOMAIN`、GitHub 三项配置、独立的 `JWT_SIGNING_KEY`。前三个搜索服务填写对应 `BRAVE_SEARCH_API_KEY`／`FIRECRAWL_API_KEY`／`EXA_API_KEY`。`BASE_URL` 只含 origin，不含 `/mcp`；所有 `REPLACE_*`、`your-*` 和示例域名必须替换。

本地生成随机值并填入受保护的文件，部署报告不记录生成值：

```bash
openssl rand -hex 32                 # JWT_SIGNING_KEY
openssl rand -base64 32 | tr '+/' '-_' # STORAGE_KEY：Fernet URL-safe base64，保留末尾 =
openssl rand -hex 32                 # REDIS_PASSWORD
```

Lark Doc 只使用 JWT 项，其余五个服务还需 `STORAGE_KEY`、`REDIS_PASSWORD`，并建立独立 Redis 环境文件：

```bash
# 仅五个 Compose 服务执行。
sudo test -e "/etc/$SERVICE.redis.env" || sudo install -o root -g root -m 600 .redis.env.example "/etc/$SERVICE.redis.env"
sudoedit "/etc/$SERVICE.redis.env"
```

两个文件中的 `<前缀>_REDIS_PASSWORD` 必须完全一致。Redis 环境文件只存 Redis 密码，不存上游凭据。环境文件每行 `NAME=value`，密钥值不包含 `$`、空格或换行；不把这些文件作为 shell 脚本执行。配置项的完整定义见对应服务的 `CONFIGURATION.md` 或 `CONFIG.md`。

Colab 的 ADC 文件必须在 Compose 启动前准备。授权与文件权限见 [Colab 配置](colab-cli-mcp/CONFIGURATION.md#3-google-授权)。

## 4. 启动服务

### 4.1. 五个 Compose 服务

```bash
sudo docker compose config --quiet
sudo python3 ../scripts/prepare-python.py "$SERVICE"
case "$SERVICE" in brave-search-mcp|firecrawl-mcp|lark-cli-mcp) sudo bash scripts/prepare-cli.sh ;; esac
sudo docker compose build --pull
sudo docker compose up -d --wait --wait-timeout 180
sudo docker compose ps -a
```

`redis-init`／`state-init` 正常应以退出码 0 结束，MCP 和 Redis 应为 healthy。应用端口仅绑定回环地址，Redis 无宿主机端口；健康检查只代表监听可用，业务验收见第 6 节。Compose 命令统一以 sudo 运行，以读取 root 所有的 0600 环境文件；不运行会把密钥展开到终端的 `docker compose config`。

`lark-cli-mcp` 接着在容器的持久卷内配置共享身份：

```bash
sudo docker compose exec mcp lark-cli config init --new
sudo docker compose exec mcp lark-cli auth login --domain all --no-wait --json
# 用户完成浏览器授权后，用实际返回的 device_code 执行：
sudo docker compose exec mcp lark-cli auth login --device-code '<device_code>'
sudo docker compose exec mcp lark-cli auth status --json --verify
```

已有应用／登录态时先检查，避免重新创建或替换共享身份。共享身份供所有 GitHub 白名单用户使用。具体权限和应用复用方式见 Lark 配置文档。

Colab 启动后按配置文档检查 ADC 权限和 `colab usage`。Colab CLI 通过 `deploy/python/uv.lock` 与 Python 依赖一起解析、冻结和升级。

### 4.2. Lark Doc systemd 服务

该服务需要固定路径的 Python 环境与 `/usr/local/bin/lark-cli`，不使用部署用户的个人 PATH。使用锁文件安装 Python 依赖，首次安装 CLI 时取官方最新稳定 release 并校验摘要；已安装时使用现有二进制：

```bash
set -euo pipefail
sudo apt-get install -y python3-venv
id lark-doc >/dev/null 2>&1 || sudo useradd --system --user-group --home-dir /var/lib/lark-doc --shell /usr/sbin/nologin lark-doc
sudo install -d -o lark-doc -g lark-doc -m 700 /var/lib/lark-doc
sudo python3 ../scripts/prepare-python.py lark-doc --native
sudo install -d -o lark-doc -g lark-doc -m 700 .lark_publish

if [ ! -x /usr/local/bin/lark-cli ]; then
  CLI_VERSION=$(curl -fsSL https://api.github.com/repos/larksuite/cli/releases/latest | python3 -c 'import json,re,sys; d=json.load(sys.stdin); assert not d.get("draft") and not d.get("prerelease"); v=d["tag_name"].removeprefix("v"); assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",v); print(v)')
  CLI_ARCH=$(dpkg --print-architecture)
  CLI_TMP=$(mktemp -d)
  CLI_ASSET="lark-cli-$CLI_VERSION-linux-$CLI_ARCH.tar.gz"
  CLI_RELEASE="https://github.com/larksuite/cli/releases/download/v$CLI_VERSION"
  curl -fsSL "$CLI_RELEASE/$CLI_ASSET" -o "$CLI_TMP/$CLI_ASSET"
  curl -fsSL "$CLI_RELEASE/checksums.txt" -o "$CLI_TMP/checksums.txt"
  (cd "$CLI_TMP" && awk -v f="$CLI_ASSET" '$2 == f {print}' checksums.txt > selected.sha256 && test -s selected.sha256 && sha256sum -c selected.sha256)
  # 仅摘要验证成功后继续。
  tar -xzf "$CLI_TMP/$CLI_ASSET" -C "$CLI_TMP" lark-cli
  sudo install -m 755 "$CLI_TMP/lark-cli" /usr/local/bin/lark-cli
  rm -rf "$CLI_TMP"
fi
/usr/local/bin/lark-cli --version
sudo -u lark-doc deploy/python/.venv/bin/python -B -c 'import tempfile; d = tempfile.TemporaryDirectory(dir=".lark_publish"); d.cleanup()'
sudo install -m 644 deploy/lark-doc-mcp.service /etc/systemd/system/lark-doc-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now lark-doc-mcp.service
sudo systemctl is-active lark-doc-mcp.service
```

所有 CLI／SDK／MCP 应用依赖首次安装从官方来源解析最新稳定版；已安装后的调试、代码修改和重建沿用现有版本，只有明确升级才重新解析。Python、Redis、BusyBox 保留本文兼容范围，不随应用升级追新。三种 CLI 的 `scripts/prepare-cli.sh` 首次写入本机 `deploy/*-cli.version`，后续读取它；旧实例没有记录时从现有容器读取版本，无法读取便停止，不擅自升级。该记录不随源码分发，更新和备份时须保留。服务代码、虚拟环境由 root 管理，只有 `.lark_publish` 与 `/var/lib/lark-doc` 供服务账户写入。systemd 的重启频率限制放在 `[Unit]`，`[Install]` 提供开机启用。

应用绑定和用户授权可在第 6 节连接后通过工具分步完成；先征得用户创建应用的同意，调用 `begin_lark_app_setup`，用户完成浏览器操作后调用 `complete_lark_app_setup`，再发起和完成用户授权。已有飞书应用复用方式见 `lark-doc/CONFIG.md`。在终端执行 CLI 时必须使用相同账户和状态路径：

```bash
sudo -u lark-doc env HOME=/var/lib/lark-doc XDG_CONFIG_HOME=/var/lib/lark-doc/config XDG_DATA_HOME=/var/lib/lark-doc/data /usr/local/bin/lark-cli auth status --json --verify
```

## 5. 发布 HTTPS 端点

初次部署先装 HTTP bootstrap，用 webroot 签证，再装正式 TLS 配置。模板渲染脚本会替换域名，保留 Nginx 的 `$host` 等变量；配置检查或 reload 失败时恢复旧文件。已存在站点时拒绝重新安装 bootstrap，避免把线上 TLS 降级。

```bash
sudo install -d -m 755 /var/www/certbot
sudo env DOMAIN="$DOMAIN" bash ../scripts/nginx.sh "$SERVICE" bootstrap
# 交互填写证书通知邮箱并接受条款。
sudo certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN"
sudo env DOMAIN="$DOMAIN" bash ../scripts/nginx.sh "$SERVICE" tls
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nnginx -t && systemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx >/dev/null
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
```

初次发布前可预览：`env DOMAIN="$DOMAIN" bash ../scripts/nginx.sh "$SERVICE" render`。Nginx 代理所有路径，包含 OAuth discovery、注册、回调和 `/mcp`；正式模板关闭代理缓冲并设置服务独立的注册限流区；Lark Doc 请求体上限为 32 MiB，容纳 20 MiB 媒体的 base64 编码。各服务端口、限流区名不能复用。签证失败时保留 bootstrap，修正公网 DNS／80 端口后重试；不要安装尚无证书的 TLS 模板。

## 6. 验收与客户端接入

从服务器外执行并检查状态码、发现文档中的 origin：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"deployment-check","version":"1"}}}'
curl -fsS "https://$DOMAIN/.well-known/oauth-protected-resource/mcp"
curl -fsS "https://$DOMAIN/.well-known/oauth-authorization-server"
```

未认证的 MCP 初始化请求应返回 401，两个发现端点应返回 200 和正确的 HTTPS URL。随后用支持 OAuth 的 MCP 客户端添加 `https://实际DOMAIN/mcp`，完成 GitHub 登录，确认白名单用户可登录、非白名单用户无法取得工具权限，执行 `tools/list`，再实际调用一个工具：

| 服务 | 最小业务验收 |
|---|---|
| Brave | 一次 `web` 查询，检查 CLI 退出码和结果 |
| Firecrawl | 一次 `search` 查询，检查退出码和结果 |
| Exa | 一次实际搜索；`tools/list` 不能证明 API Key 有效，可额外运行 `sudo scripts/apikey.sh verify` |
| Lark CLI | 检查身份后读取一个已授权资源，检查退出码 |
| Colab CLI | `usage` 后创建命名 CPU 会话，执行 `print(2 + 2)`，验证后停止该会话 |
| Lark Doc | `check_lark_cli` 后读取一个已授权测试文档 |

业务调用可能消耗上游额度。读写能力验收使用明确授权的测试文档。CLI 客户端例如：

```bash
claude mcp add --transport http "$SERVICE" "https://$DOMAIN/mcp"
codex mcp add "$SERVICE" --url "https://$DOMAIN/mcp"
codex mcp login "$SERVICE"
```

GUI 客户端添加远程 URL 并完成 OAuth；具体回调兼容范围见各服务文档。部署报告记录系统、架构、代码 commit、镜像 ID／CLI 版本、配置文件路径、服务名、MCP URL、验收结果；不记录凭据或 OAuth code。

### 6.1 客户端回调白名单

六个服务共用同一份 `ALLOWED_CLIENT_REDIRECT_URIS`，只有 WorkBuddy 回调按服务区分。FastMCP 4 在动态注册（DCR）和 CIMD 时逐个校验客户端提交的回调，任意一个不在白名单内整次注册返回 400，客户端通常只提示“重定向网址被拒绝”或要求手填客户端 ID／密钥。GitHub OAuth App 的回调仍填写服务自己的 `https://实际DOMAIN/auth/callback`。

| 客户端 | 回调 |
|---|---|
| Claude 网页版、Desktop 连接器、移动端、Cowork | `https://claude.ai/api/mcp/auth_callback`、`https://claude.com/api/mcp/auth_callback` |
| Claude Desktop 插件 | `claude://claude.ai/mcp-auth-callback/sdk` |
| ChatGPT／Codex 托管连接器 | `https://chatgpt.com/connector_platform_oauth_redirect`、`https://chatgpt.com/connector/oauth/*` |
| Gemini 网页版 | `https://{oauth-redirect,oauth-redirect-sandbox,oauth-redirect-test}.googleusercontent.com/{r,a}/user_bound_custom-mcp-*`，一次注册同时提交这 6 个地址 |
| Gemini Enterprise | `https://vertexaisearch.cloud.google.com/oauth-redirect` |
| Grok | `https://grok.com/connectors-oauth-exchange-code/`、`https://console.x.ai/connectors-oauth-exchange-code/` |
| Cursor 网页版／Cloud Agents、旧版桌面端 | `https://cursor.com/agents/mcp/oauth/callback`、`https://www.cursor.com/agents/mcp/oauth/callback`、`cursor://anysphere.cursor-mcp/oauth/callback` |
| VS Code 网页版 | `https://vscode.dev/redirect` |
| 本机客户端：Claude Code、Codex CLI、Gemini CLI、Cursor 桌面端、VS Code、MCP Inspector 等 | `http://localhost:*`、`http://127.0.0.1:*`、`http://[::1]:*`，任意端口和路径 |

同一份兼容层还处理 FastMCP 4 的两处行为：以 `client_secret_post`／`client_secret_basic` 注册的客户端会拿到并被校验 `client_secret`（Gemini 网页版需要），授权服务器元数据同时列出这两种方式以及 `none`、`private_key_jwt`。`mcp/tests/test_oauth_clients.py` 覆盖白名单一致性和上表各客户端的注册；新增客户端时先把它的完整回调集合加入该测试和全部服务的白名单。

在 Gemini 网页版添加自定义 MCP，填入 `https://实际域名/mcp`，在授权页完成 GitHub 登录。[Google OAuth 2.1 接入指南](https://developers.google.com/connected-apps/mcp/oauth)。注册仍被拒绝时，先确认运行中的镜像或 systemd 进程已加载新代码；服务关闭了访问日志，需要定位时在反向代理临时记录 `/register` 的状态码，排查后关闭并删除日志。验收需确认发起授权后能打开同意页，最终完成 GitHub 登录并执行一次第 6 节的最小业务调用。

## 7. 运维、备份与回滚

| 操作 | Compose | Lark Doc |
|---|---|---|
| 查看状态 | `sudo docker compose ps -a` | `sudo systemctl status lark-doc-mcp` |
| 最近日志 | `sudo docker compose logs --tail 100 mcp` | `sudo journalctl -u lark-doc-mcp -n 100 --no-pager` |
| 配置生效 | `sudo docker compose up -d --force-recreate --wait` | `sudo systemctl restart lark-doc-mcp` |
| 停止 | `sudo docker compose stop` | `sudo systemctl stop lark-doc-mcp` |

Compose 升级前在服务目录保存旧镜像：

```bash
ROLLBACK_IMAGE="$SERVICE:rollback"
sudo docker image tag "$(sudo docker compose images -q mcp)" "$ROLLBACK_IMAGE"
```

记录代码 commit 和当前镜像 ID，停止服务后备份两个 `/etc` 环境文件和 Compose 命名卷；三种 CLI 服务还备份对应的 `deploy/*-cli.version`；所有 MCP 备份 `deploy/python/`；Colab 还需随卷备份只读挂载的 ADC 文件；Lark Doc 备份 `/etc/lark-doc.env`、`/var/lib/lark-doc`。代码、锁文件、CLI 版本与备份一起记录；存储密钥必须与 Redis 数据配套恢复。备份加密存储，复制命名卷时保持 UID／GID，恢复到同一 Compose 项目路径。升级或停止均不执行 `down -v`。

Compose 升级：更新代码后在服务目录运行测试和 `config --quiet`。只有 MCP 代码变化且依赖服务健康时，可以仅构建并更新 `mcp`，沿用镜像缓存和本机版本记录。涉及 Compose 拓扑、依赖服务或存储格式变更时，按第 4 节启动完整项目并验证相关服务。

```bash
sudo docker compose config --quiet
sudo docker compose build mcp
sudo docker compose up -d --no-deps --no-build --pull never --wait --wait-timeout 180 mcp
```

更新后重做第 6 节验收。失败时恢复旧代码，并用旧镜像 tag 的 Compose override 替换 `mcp.image`。涉及存储格式变化时同时恢复配套卷与环境文件。回滚命令在原服务目录执行。下例针对标准 `compose.yaml`，已有本地 override 时在 `-f compose.yaml` 之后、回滚 override 之前一并传入。

```bash
printf 'services:\n  mcp:\n    image: %s:rollback\n' "$SERVICE" > /tmp/mcp-rollback.yaml
sudo docker compose -f compose.yaml -f /tmp/mcp-rollback.yaml up -d --no-build --wait
```

后续操作继续传同一 override，直到再次部署新版本；保存的 rollback tag 在下一次覆盖前已完成本轮验收。

Lark Doc 升级：停止服务、记录旧 CLI 和虚拟环境，更新代码后使用现有 `deploy/python/.venv` 测试和运行，不重新解析依赖。测试后重启，等待本机 `http://127.0.0.1:8765/.well-known/oauth-authorization-server` 返回 200，再进行第 6 节验收。为 Python 导入及启动预留最多 60 秒，超时后查服务日志并按失败处理。回滚时恢复旧代码／锁文件及 CLI，再同步旧依赖和重启。OAuth 数据存储位置跟随 FastMCP 的默认实现，`/var/lib/lark-doc` 整体备份；恢复后仍需验证客户端会话。

### 7.1. 应用依赖版本规则

`prepare-python.py` 首次解析官方 PyPI 的最新稳定应用依赖并运行单元测试，成功后保存 `deploy/python/`（项目依赖、锁文件、uv 版本和安装元数据）；Docker／systemd 后续复用该记录。已有实例没有记录时，脚本从运行中的容器或原生 `.venv` 读取已安装包版本并按原版本建立记录，无法读取便停止。不要删除记录后以“首次安装”方式进行调试。

显式升级应用依赖时，先保留原镜像／原生环境和记录；原生服务先停止。然后执行：

```bash
sudo python3 ../scripts/prepare-python.py "$SERVICE" --upgrade
# lark-doc 使用：
# sudo python3 ../scripts/prepare-python.py lark-doc --native --upgrade
```

新依赖先在候选环境测试，通过后才切换记录，旧记录保留在 `deploy/python.backup-*`。Compose 随后重新 build／up；原生服务重新启动。回滚恢复原镜像和对应依赖记录；原生记录须恢复到原路径以保持虚拟环境入口有效。CLI 升级使用各服务 `scripts/update-cli.sh`，不会顺带升级 Python 应用依赖。

只升级单个依赖（例如 FastMCP）时，在候选目录复制 `deploy/python/` 的 `pyproject.toml` 和 `uv.lock`，用 `uv-version.txt` 记录的 uv 版本执行 `uv lock --upgrade-package <包名>`。由运行中实例采纳而来的记录在 `constraint-dependencies` 中固定了全部已安装版本：先删除目标包的约束，解析失败时只删除报错中阻塞的约束并重试，其余保持不变。随后 `uv sync --frozen` 候选环境，用它运行服务测试和 `mcp/tests/test_oauth_clients.py`，通过后备份原记录再替换锁文件与 `pyproject.toml`，Compose 重新 build／up，原生服务在原路径 `uv sync --frozen` 后重启。

版本解析默认不接受预发布版；保持平台范围的前提下由解析器选择兼容的最新稳定依赖。首次解析后使用本机记录进行复现，不把部署记录提交回公开仓库。

## 8. 故障定位

| 现象 | 检查顺序 |
|---|---|
| 脚本找不到共享文件 | 是否完整复制 `mcp/`，从 `/opt/mcp/<服务>` 执行 |
| 预检发现其他反向代理 | 确认 80／443 的真实监听进程，统一入口配置 |
| Compose 无权限读取 env | 使用 sudo，检查 root 所有和 0600 权限 |
| Redis 不健康 | 两个文件的密码是否一致、卷权限、磁盘与日志 |
| 证书申请失败 | 公网 A／AAAA、外部 80 可达、webroot 和站点域名 |
| Nginx 检查失败 | 占位符、证书路径、重复 zone／server_name、`nginx -t` 输出 |
| 502 或容器启动失败 | 服务日志、依赖、回环端口和必填配置 |
| systemd 启动被限制 | 修复日志指出的问题后 `sudo systemctl reset-failed lark-doc-mcp` 并重启 |
| GitHub OAuth App 回调失败 | App callback、BASE_URL、公开域名、全路径反向代理是否一致 |
| 客户端提示“重定向网址被拒绝”或要求手填客户端 ID／密钥 | `/register` 返回 400 `invalid_redirect_uri` 时，把错误中的地址连同该客户端的全部回调加入第 6.1 节白名单；确认运行中的镜像或进程已加载新代码 |
| 飞书身份异常 | 是否用相同账户和 HOME／XDG，持久卷是否恢复，scope 是否授权 |

## 9. 上游依据与验证边界

安装方法依据 [Docker Ubuntu 文档](https://docs.docker.com/engine/install/ubuntu/)和 [Docker Debian 文档](https://docs.docker.com/engine/install/debian/)，systemd 单元依据 [systemd.unit](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html)，Python 安装路径依据 [uv 环境变量](https://docs.astral.sh/uv/reference/environment/#uv_python_install_dir)，证书使用 [Certbot webroot](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)。部署 Python 依赖以本机 `deploy/python/uv.lock` 为准，源码 `uv.lock` 是维护时的开发参考；CLI 版本以本机 `deploy/*-cli.version` 为准，容器基础镜像仍使用版本标签；严格逐位复现需在实际构建后记录镜像 digest 并使用 digest 部署。

### 9.1 生成并审核分发包

在仓库根目录先同步当前分支的远端引用，再开始修改。提交前按 `AGENTS.md` 检查变更范围、执行测试，并复核最终 diff 和公开信息。示例值使用明确的占位符。真实主机名、账户标识、Gemini 连接器 ID 和凭据仅保存在目标环境。

```bash
bash mcp/scripts/check-python.sh --runtime
python3 -B skills/scripts/check_public_sources.py
python3 -B -m unittest discover -s mcp/tests -v
git diff --check
git diff --cached --check
```

从已审核的提交生成 MCP 归档，并检查实际压缩包。归档只取该提交记录的 `mcp/`，包含共享脚本、隐藏示例和锁文件。扫描会检查凭据和标识的常见形态、链接、二进制文件以及本机运行数据或版本记录。审核最终清单和内容后，随包交付校验文件。源检查脚本留在仓库侧。

```bash
(
  set -euo pipefail
  commit=$(git rev-parse --verify 'HEAD^{commit}')
  package="mcp-${commit:0:12}.tar.gz"
  mkdir -p dist
  staging=$(mktemp -d dist/.mcp-release.XXXXXXXX)
  trap 'rm -rf -- "$staging"' EXIT
  git archive --format=tar.gz --output="$staging/$package" "$commit" mcp
  python3 -B skills/scripts/check_public_sources.py --archive "$staging/$package"
  (cd "$staging" && sha256sum "$package" > "$package.sha256")
  mv -- "$staging/$package" "$staging/$package.sha256" dist/
)
```

将归档及校验文件放在同一目录，在目标服务器运行 `sha256sum -c mcp-<提交标识>.tar.gz.sha256`，将文件名替换为实际交付文件。校验通过后按第 2 节部署。

### 9.2 测试与生产验收

本地验证命令：

```bash
python3 -B -m unittest discover -s /opt/mcp/tests -v
# OAuth 注册契约需要服务自身依赖。在 mcp/ 目录（如 /opt/mcp）用该服务的虚拟环境逐个运行；
# 在仓库根目录运行会与 mcp 依赖包重名而导入失败。虚拟环境由服务 README 开发验证步骤中的
# prepare-python.py --native 创建。
cd /opt/mcp
MCP_SERVICE="$SERVICE" "$SERVICE/deploy/python/.venv/bin/python" -B -m unittest tests.test_oauth_clients
```

第一条在没有服务依赖时跳过注册契约，只做白名单一致性检查。各项目的开发测试命令见各自 README。模板测试检查自定义域名、回环端口、Nginx 变量保留和同机限流区唯一性。

本地语法、模板和单元测试不等同于生产部署验收。Ubuntu／Debian 的真实安装、证书签发、GitHub／飞书／Google 交互授权和外部客户端业务调用，以目标服务器第 6 节的记录为最终结果。
