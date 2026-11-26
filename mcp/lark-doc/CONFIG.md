# lark-doc MCP 配置

部署入口：[统一 Linux 部署运行手册](../DEPLOYMENT.md)。首次部署与升级以该手册为准；本文保留服务配置、工具边界和客户端参考。

可分发的 uv 项目：支持飞书电子表格、多维表格及把 Markdown 内容写入飞书 Docx，支持并发批量拉取、并发批量推送、定点修改、创建文档、插入媒体及画板读写。公网部署后可由 ChatGPT、Claude.ai、Claude Desktop、Claude Code 和 Gemini 网页版通过 OAuth 2.1 连接。

> **域名配置**：本文档所有 `{your-domain}` 需替换为实际部署域名（如 `lark-doc.example.com`）。

本文件提供本服务的配置和工具契约；Linux 安装与部署以统一运行手册为准。

## 1. 环境要求

本地开发支持 macOS／Linux，应用使用 Python 3.11+；准备脚本需要 Python 3.10+、venv／ensurepip，并自行管理隔离 uv，无需全局安装 uv。先按[统一部署预检](../DEPLOYMENT.md#21-python-与非默认工具预检)确认运行环境。服务器的 Python／uv 按运行手册安装，原生 `lark-cli` 首次取最新稳定 release，已安装后沿用现有版本，不依赖 Node.js 或部署用户的个人 PATH。CLI 必须支持 `config init --new`；升级前通过能力验收。

## 2. 飞书应用创建与绑定

每个独立部署必须先让服务器端 `lark-cli` 绑定一个飞书应用；该应用提供开放接口凭证与 scope，不是飞书智能体。开发机已有配置不会随项目分发，必须在目标服务器上以最终服务账户完成一次。

推荐通过 MCP 完成无密钥创建流程：

1. `check_lark_cli` 返回 `app_not_configured` 后，先征得用户同意创建应用。
2. 调用 `begin_lark_app_setup(confirmation="CREATE_LARK_APP")`。服务在后台运行 `lark-cli config init --new`，返回原始配置 URL 和 PNG 二维码；把两者交给用户，本轮不要等待。
3. 用户确认浏览器显示配置成功后调用 `complete_lark_app_setup`；只有返回 `status=configured` 才算绑定完成。
4. 再调用 `begin_lark_auth` / `complete_lark_auth`，完成 Docs、Drive、Wiki、Sheets、Base 用户授权。

`begin_lark_app_setup` 检测到已有配置时返回 `already_configured`，不会覆盖。它只支持独立 MCP 的新应用创建；`lark-cli config bind` 专用于 OpenClaw、Hermes、Lark Channel 等 Agent 工作区，不应拿来绑定本服务。若必须复用已有飞书应用，由管理员在服务器真实终端通过 `config init --app-id ... --app-secret-stdin` 配置，App Secret 不得作为 MCP 参数或写入命令历史。

完成应用绑定后再授权用户身份；文档、云空间、Wiki、电子表格和多维表格操作需要应用后台 scope 与用户授权同时具备：

```bash
lark-cli auth login --domain docs --domain drive --domain wiki --domain sheets --domain base --no-wait --json
lark-cli auth status --json --verify
```

授权命令返回验证链接时在浏览器完成授权。缺少 scope 时按 CLI 错误里的 `console_url` 在开发者后台开通，再重新执行最小范围授权。systemd 部署时，应用绑定和用户授权都必须以服务账户、下文同一组 `HOME`/XDG 路径完成；不要以部署账户配置后再切换服务账户。

服务不会自动升级；首次安装使用最新稳定 CLI，之后调试和修改使用当时已安装版本，明确升级时再更换并验证能力。CLI 在 JSON 中返回 `_notice.update` 时，`check_lark_cli` 会把它作为 `update_notice` 转交客户端；当前操作继续执行，由用户决定是否手动运行 `lark-cli update`。

## 3. 安装与测试

以下为本地开发环境准备，保留项目 `uv.lock`；服务器复制范围按统一部署手册执行：

```bash
cd mcp/lark-doc
python3 ../scripts/prepare-python.py lark-doc --native
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

## 6. 远程客户端 OAuth

客户端不会从服务器“自动安装”MCP。它们连接公开 HTTPS `/mcp` 端点；OAuth 客户端可通过 CIMD 或 DCR 自动登记。当前实现使用 FastMCP 的 GitHub OAuth 代理提供 OAuth 2.1、PKCE、CIMD/DCR 和 protected-resource discovery，并只放行 `LARK_MCP_GITHUB_USERS`。

先在 GitHub 创建 OAuth App：

- Homepage URL：`https://{your-domain}`
- Authorization callback URL：`https://{your-domain}/auth/callback`

环境变量在服务进程启动前由 systemd 的 `EnvironmentFile` 加载；使用运行手册的 `.env.example`，首次创建且保留已有文件，不在普通 shell 中读取 root 所有的环境文件。

Ubuntu／Debian 的完整安装、服务账户、固定 PATH、目录权限、Nginx 模板、证书续期与升级回滚，统一执行 [部署运行手册](../DEPLOYMENT.md)。完整单元文件为 `deploy/lark-doc-mcp.service`，代码路径为 `/opt/mcp/lark-doc`，状态目录为 `/var/lib/lark-doc`。

服务的 `NoNewPrivileges=true` 禁止通过 sudo 提权，默认部署下 `schedule_mcp_restart` 无法完成提权重启；管理员使用 `sudo systemctl restart lark-doc-mcp`。其他文档操作不依赖该工具。服务运行时仅 `.lark_publish` 和状态目录可写，升级由管理员管理代码和虚拟环境。

在支持远程 MCP OAuth 的客户端创建连接器，填写 `https://{your-domain}/mcp` 并完成 GitHub 授权；客户端界面以当前版本为准。

Claude.ai、Claude Desktop、移动端和 Cowork 使用 OAuth 回调 `https://claude.ai/api/mcp/auth_callback`。在客户端的远程 MCP 设置中添加 URL：`https://{your-domain}/mcp`。

WorkBuddy 的 Custom MCP 使用固定回调 URI `workbuddy://workbuddy/mcp/custom-mcp%3Alark-doc/oauth/callback`；服务已将该精确 URI 加入 OAuth 白名单。添加连接器后按 WorkBuddy 的 GitHub 授权流程完成登录。

Claude、ChatGPT／Codex、Gemini、Grok、Cursor 等客户端的回调由六个服务共用的[客户端回调白名单](../DEPLOYMENT.md#61-客户端回调白名单)统一管理。新增客户端时先在该白名单和 `mcp/tests/test_oauth_clients.py` 中加入它的完整回调集合。

Claude Code 直接添加远程 HTTP MCP；它会发现 OAuth 并在 `/mcp` 中引导认证：

```bash
claude mcp add --transport http lark-doc https://{your-domain}/mcp
```

Claude Code 使用动态 localhost 回调端口；服务器已允许 `localhost` 和 `127.0.0.1` 回环地址。

## 7. 直接 TLS 模式

默认使用 Nginx 终止 TLS。需要直接 TLS 时，服务支持 `--tls-cert`／`--tls-key`；`LARK_MCP_BASE_URL` 必须包含真实公开端口。管理员还需配置服务账户对证书的读取权限、证书续期和服务重启，不能直接复用仅 root 可读的私钥路径。

## 8. 静态 Token 模式

该模式用于 Codex 或支持自定义 Bearer Token 的 MCP 客户端；ChatGPT 和 Claude 托管端使用 OAuth：

密钥管理脚本故意拒绝非交互运行。以下命令必须由用户本人在真实终端执行；Agent 不得执行、代输确认语或读取输出：

```bash
sudo -u lark-doc /opt/mcp/lark-doc/deploy/python/.venv/bin/python \
  /opt/mcp/lark-doc/scripts/manage_secret_key.py init /var/lib/lark-doc/auth.token
# 轮换：把 init 改为 rotate
# 手动查看：把 init 改为 show
```

在 `/etc/lark-doc.env` 中配置并重启 systemd 服务，单纯在终端 `export` 不会改变现有服务的环境：

```dotenv
LARK_MCP_AUTH_MODE=token
LARK_MCP_AUTH_TOKEN_FILE=/var/lib/lark-doc/auth.token
```

客户端的 `LARK_MCP_AUTH_TOKEN` 环境变量应由操作者设为同一 token，再执行：

```bash
codex mcp add lark-doc \
  --url https://{your-domain}/mcp \
  --bearer-token-env-var LARK_MCP_AUTH_TOKEN
```

若条目已存在，更新该条目的 URL／认证配置；不要删除其他连接。Token 本身不写入仓库配置或命令历史。

## 9. 验证

部署验收按运行手册：未认证请求应返回 `401`；已认证的普通浏览器 GET 仍可能因 Accept 头不符合协议而返回 `406`；用已授权客户端列出工具并对测试文档做一次真实读写。

评论功能单独使用已有评论的测试文档做只读验收。调用 `list_document_comments`，传入文档或 Wiki URL、`solved_status=all` 和较小的 `page_size`，检查返回的评论正文、引用与定位字段。若 `has_more=true`，用 `page_token` 读取下一页。选择一条评论的 `comment_id`，用 `list_document_comment_replies` 核对首条消息与原评论是否一致。调用 `batch_pull(include_comments=true)` 检查随文返回的评论内容。

## 10. MCP 工具

- `check_lark_cli`：检查 CLI、版本和用户登录态。
- `begin_lark_app_setup`、`complete_lark_app_setup`：经用户明确确认后创建并绑定新飞书应用；分步返回配置 URL/二维码并验收浏览器配置结果，不接收 App Secret。
- `schedule_mcp_restart`：在当前调用完成后延迟重启固定的 MCP 服务；必须传入确认词 `RESTART_LARK_DOC_MCP`，延迟范围为 5–300 秒。
- `begin_lark_auth`、`complete_lark_auth`：发起和完成飞书用户授权。
- `batch_pull`：批量读取 Markdown/XML 与 revision。普通正文使用 Markdown `simple`；当任务依赖 Markdown 无法可靠呈现的格式或原生结构时，使用 XML `full`，包括高亮、文字/背景颜色、下划线、Callout、分栏、引用、书签、URL 预览、按钮、提醒、画板，以及 block ID、样式属性和引用元数据。高亮在 XML 中表示为 `<span background-color="...">`，在 Markdown 中会降级为纯文本。长文档用 `scope` 缩小读取范围：`outline` 只列标题（`max_depth` 限制层级），`keyword` 按关键词取块（`a|b` 表示或），`section` 从标题 `start_block_id` 展开，`range` 读取 `start_block_id` 到 `end_block_id`；后两种只接受一篇文档。
- `search_documents`：按关键词搜索 Docs、Wiki 和 Sheets，返回相关性排序的候选、摘要和资源标识；`count` 为 1–20。目标文档未知时先搜索，再定位正文。
- `batch_pull(include_comments=true)`：随正文返回 CLI 提取的 `comments`，其中每项的 `data` 包含评论内容，`tips` 在 CLI 截断评论时提供提示。
- `list_document_comments`：分页读取评论正文、已包含的回复、引用文本和 Docx 定位信息。支持文档或 Wiki URL，传裸 token 时提供 `file_type`。正文在 `items[].reply_list.replies[0].content.elements`。默认查未解决评论，`solved_status=all` 同时查已解决评论。顶层 `has_more=true` 时传 `page_token` 继续。
- `list_document_comment_replies`：按 `comment_id` 分页读取完整评论线程。第一页首条是原评论，`items[].content.elements` 保留文字、图片等原始元素。指定 Wiki URL 时，两项评论工具需要飞书评论读取权限 `docs:document.comment:read` 和 Wiki 节点读取权限 `wiki:node:retrieve`。
- `find_document_text`：按精确文本返回有限上下文，不向模型返回全文；用于局部编辑前定位句子。传 `doc_format=xml`、`detail=with-ids` 时片段带 block ID，供 `block_update` 和 `insert_media` 定位。
- `batch_push`：批量覆盖或追加 Markdown/XML；Markdown 写入前按“10.1. 写入约定”规整（代码块中的字面量不转换）。lark-cli 报告写入失败时立即报错，不中继为成功。写入的高亮块 emoji 被飞书替换为 💡 时，结果带 `format_warnings`。
- `point_update`：仅在旧文本唯一命中时精确替换或删除；`pattern` 或 `replacement` 含块级标签（如 `<h2>`、`<p>`、`<callout>`）时直接拒绝，改用 `block_update`，避免飞书丢弃块属性却报告成功；重复命中时先调用 `find_document_text` 缩小目标。lark-cli 报告写入失败时抛错，并附带失败后的文档状态（revision、替换文本是否已部分写入、原文尾部是否完整），用于区分"未生效"与"部分应用/截断"。
- `batch_point_update`：单篇文档内先预演全部唯一文本替换，再以 `revision_id` 串行写入；可传 `expected_revision_id` 拒绝过期定位。任一写入被 lark-cli 报告失败时立即停止（不继续写入可能已损坏的文档），并报告失败后的文档状态与已完成项。
- `block_update`：按 block ID 整块替换、插入、删除、移动或复制（`block_replace`、`block_insert_after`、`block_delete`、`block_move_after`、`block_copy_insert_after`），默认 XML；可传 `expected_revision_id`，服务器写入前先读取当前 revision，不一致即以 `revision_conflict` 拒绝（lark-cli 的块命令不按 `--revision-id` 拒绝过期写入，读取与写入之间仍有极短竞争窗口）；写入的高亮块 emoji 被替换时返回 `format_warnings`。`point_update` 只换文字、改不了块属性，修改标题编号、列表类型等结构用本工具。

> **写失败处理**：lark-cli 的写入接口可能以退出码 0 返回 `result:"failed"`（如 `str_replace` 未命中 pattern 时的降级路径），并可能对文档造成部分应用或截断。服务器对所有写操作校验该结果字段：任何显式失败都会以错误返回并附上失败后的文档状态。调用方收到写失败后必须先 `find_document_text` 核实，再决定是否重试——当状态显示写入已部分生效或文档尾部丢失时**不要重试**，否则会叠加损坏。
- `create_document`：在个人空间或 Drive 文件夹创建普通 Docx；Markdown 按“10.1. 写入约定”规整，高亮块 emoji 被替换时返回 `format_warnings`。
- `create_wiki_node`：在指定 Wiki 空间根目录或父节点下创建空白 Docx 页面。
- `create_wiki_space`：创建 Wiki 空间。
- `scan_document_assets`：在服务器端扫描飞书文档完整 XML，只返回图片和画板元数据、token 与数量。
- `insert_media`：从 base64 插入图片或附件并删除本地载荷。lark-cli 只能追加到文末；传 `after_block_id` 时再用 `block_move_after` 移到该块之后（`0` 为文档开头）。移动失败时报 `media_inserted_not_moved`，只重试移动，不要重传。
- `whiteboard_query`、`whiteboard_update`：读取和更新飞书画板。

### 10.1. 写入约定

Markdown 写入（`batch_push`、`create_document`、`point_update` 的替换文本、`block_update`）前，服务器先做三项规整，XML 写入原样发送：

- 标题编号：`## 1. 标题`、`### 1.2 标题` 这类手写编号的标题转成飞书自动编号 `<h2 seq="auto">标题</h2>`，标题内的加粗、行内代码、链接等同步转成 XML。不转换的情况：文档开头作为标题的 `# `、没有点号的数字（如 `## 2024 年总结`）、代码块内容。层级须连续，不跳级。
- 强调标点：`*`、`**` 内侧是标点且外侧紧贴文字时，把标点移到标记外，例如 `abc**"q"**def` 变为 `abc"**q**"def`、`**重点：**说明` 变为 `**重点**：说明`。被移出的标点不再加粗；需要加粗时改用 XML `<b>`。
- 独立 `$$...$$` 公式写为居中的原生公式段落。

实测（2026-11-16，lark-cli 1.0.99）：飞书能正确渲染 `中文**“引用”**中文`；`abc**"q"**def`、`文字*“x”*文字`、`文字__x__文字` 不渲染。`__x__` 不自动转换，服务说明要求一律写 `**x**`。`<a type="url-preview">` 读回时总是普通链接，无法据此判断预览是否生效，需要在页面上目视确认。

### 10.2. 创建 Wiki 的选择

| 目标 | 工具 | 必填参数 | 创建结果 |
|---|---|---|---|
| 新 Wiki 空间 | `create_wiki_space` | `name` | 一个独立 Wiki 空间，没有页面 |
| Wiki 空间根目录的页面 | `create_wiki_node` | `title`、`space_id` | 一个带空白 Docx 的 Wiki 节点 |
| 已有 Wiki 页面的子页面 | `create_wiki_node` | `title`、`parent_node_token` | 父节点下的一个带空白 Docx 的 Wiki 节点 |
| Drive 文件夹中的普通文档 | `create_document` | `content`，可选 `parent_token` | 一个普通 Docx，不创建 Wiki 节点 |

`parent_node_token` 是 Wiki 节点 token；`parent_token` 是 `create_document` 的 Drive 文件夹 token。两者不可混用。创建 Wiki 页面后，用返回的 Docx token/URL 调用 `batch_push` 写入 Markdown，并回读确认正文和层级。

错误为结构化 JSON，包含操作名；批量错误还包含失败索引、文档标识、已完成数量和底层 `cause`。临时载荷清理失败附加到原始错误，不覆盖真正失败原因。

## 11. 分发检查

从[已审核的 MCP 分发包](../DEPLOYMENT.md#91-生成并审核分发包)部署。仓库维护者先检查公开源，再检查实际归档。

```bash
bash ../scripts/check-python.sh --setup
python3 ../scripts/prepare-python.py lark-doc --native
```

如需额外审计依赖，先用 `bash ../scripts/check-python.sh --uv` 检查 uv，再在独立审计环境使用 pip-audit；不在运行环境安装审计工具或重解析依赖。

仓库不包含 Token、飞书凭据、证书、`.venv`、缓存或 `.lark_publish` 运行数据。

## 12. 电子表格与多维表格

沿用 Docx 的用户身份、认证、结构化错误及工具注解。操作类型、读取类别、字段类型、视图类型、选项颜色、单元格对齐和换行方式以 JSON Schema 枚举提供候选值；分页和返回长度以 Schema 声明边界。

已有授权需重新执行 `begin_lark_auth` / `complete_lark_auth`，纳入 `sheets` 和 `base` 权限域；缺少应用 scope 时按 CLI 返回的后台链接开通。本次代码修改不会自动发起授权或改写远端表格。

| 工具 | 参数与行为 |
|---|---|
| `sheets_info` | `spreadsheet` 接受 URL/token，列出所有子表、ID、名称、行列数及可见性 |
| `sheets_create` | `title`、可选二维 `values` 和 `folder_token`，创建工作簿 |
| `sheets_read` | `cell_range` 为 A1 区域，`sheet_id` / `sheet_name` 二选一；`include` 候选含 value/formula/style/comment/data_validation/conditional_format/truncation；`max_chars` 默认 25000、上限 500000 |
| `sheets_write` | 二维 `cells`，每格支持 value/formula/cell_styles/note；默认拒绝覆盖非空格，显式 `allow_overwrite=true` 才允许；`copy_to_range` 可复制模板并平移公式引用 |
| `sheets_manage_sheet` | `action` 候选 create/rename/copy/delete/hide/unhide；create/rename 要求 title；已有子表操作需 ID/名称 |
| `sheets_clear` | `scope` 候选 content/formats/all，清除指定区域 |
| `sheets_verify_formulas` | 校验本次写入区域；AI 公式设 `ai_only=true`，返回 pending 时继续后台计算 |
| `base_inspect` | `resource` 候选 base/tables/fields/views；fields/views 要求 `table_id`，字段列表包含选项配置 |
| `base_create` | `name`、可选 folder_token/time_zone，创建 Base |
| `base_manage_table` | create 要求 name+fields，首字段为主字段；rename 要求 table_id+name；delete 要求 table_id |
| `base_manage_field` | get/create/update/delete；definition 包含 name、type、multiple、options、style，允许高级字段属性；update 为完整覆盖，先 get 并保留需要的配置 |
| `base_query_records` | record_ids 精确读取；keyword+search_fields 关键词查询；filter_config/sort 筛选排序；fields 投影；offset/limit 分页，每页 1–200 条，保留 has_more/next_offset |
| `base_write_records` | create 接受字段映射数组 records；update 接受记录 ID 到字段映射的 updates；delete 接受 record_ids；每批 1–200 条 |
| `base_manage_view` | get/create/rename/delete/set_filter/set_sort/set_group/set_visible_fields；视图类型 grid/kanban/gallery/calendar/gantt |

`base` 接受 token 或 URL，URL 通过 CLI 解析；`table_id`、`field_id`、`view_id` 也可使用 CLI 支持的精确名称。多项候选同名时使用真实 ID。记录单选值也使用数组，例如 `{"状态":["待办"]}`；字段定义为 `{"name":"状态","type":"select","multiple":false,"options":[{"name":"待办","hue":"Blue"}]}`。

视图配置示例：筛选 `{"logic":"and","conditions":[["状态","==","待办"]]}`；排序 `{"sort_config":[{"field":"截止时间","desc":false}]}`；分组 `{"group_config":[{"field":"状态","desc":false}]}`；可见字段 `{"visible_fields":["名称","状态"]}`。

写入工具提供 `dry_run` 预览（工作簿/Base 创建除外）。删除、清空及字段完整覆盖需 `confirmed=true`，该值表示调用者已获得用户对目标操作的授权。所有调用固定用户身份，不提供任意 CLI 参数或服务器文件路径；JSON 载荷上限 128 KiB，超出须拆分。

写入失败可能已有部分内容生效，须用对应读取工具核对再重试；创建与追加不保证幂等。普通公式须校验本次范围，分页与截断须按返回信息继续读取。本次覆盖在线表格的常用操作，未封装图表、透视表、自动化、附件导入导出等高级命令。

## 13. 名称迁移

项目目录、Python 项目名、MCP 公告名、systemd 单元及账户、Nginx 模板和共享部署脚本统一使用 `lark-doc`，版本为 `0.19.0`；环境变量继续使用 `LARK_MCP_*`。此变更仅修改源码与部署模板，不会停止或迁移已运行的服务。

已有部署升级时，先停止旧单元并备份旧环境文件、服务账户 HOME（含 OAuth 状态和 CLI 凭据）及 `deploy/python/`；将配置和状态迁入新模板对应的 `/etc/lark-doc.env`、`/var/lib/lark-doc`，更新所有者后安装新单元。保留存储与签名密钥，避免丢失会话；不要清空状态后重新初始化。原有依赖版本沿用，移动过的虚拟环境入口脚本需按已有依赖记录重建。确认新服务及 OAuth 回调正常后，再移除旧单元。

客户端连接器名称改为 `lark-doc`，WorkBuddy 的固定回调 URI 随之改变；若公开域名也更换，同步更新 GitHub OAuth App callback 与 `LARK_MCP_BASE_URL`。旧的服务名、账户及域名不会被代码自动删除或切换。

## 14. 行列、区域、查找替换与条件格式

新增工具固定使用已授权用户身份，复用现有 `sheets` 权限域，无须为这些工具增加新的认证参数。工具说明统一包含用途、执行语义、返回值及错误处理；每个参数在 MCP 输入 Schema 中有独立说明，候选值、必填项、默认值、数值／长度边界和条件格式专用参数均随工具列表公开。

### 14.1. 公共参数与返回值

- `spreadsheet`：电子表格 URL 或 token，不接受 Docx/Base 作为电子表格。
- `sheet_id` / `sheet_name`：二选一；先通过 `sheets_info` 获取真实子表 ID 或精确名称。跨表复制、移动的 `target_sheet_id` 限于同一工作簿。
- `cell_range` / `source_range` / `target_range`：无子表前缀的 A1 范围；目标区域可按工具说明传锚点，如 `F2`。行列结构参数使用独立语法：行 `3:7` / `5`，列 `C:F` / `C`，区间两端均包含。
- `dry_run=false` 执行；设为 `true` 时仅返回 CLI 请求预览。删除行列／条件格式执行时还需 `confirmed=true`，表示用户已授权指定目标；预览不要求确认。预览可能仍需已配置的 CLI 身份。
- 返回 CLI 的 JSON 信封：`ok`、`identity`、操作对应的 `data` 及可选 `meta`。预览结果不表示写入完成；不额外承诺 CLI 未提供的返回字段。
- 参数错误在本地拒绝，权限不足、超时和远端失败沿用结构化错误。写入失败可能已部分生效；先读取目标状态，再决定是否重试，不能盲目重发结构性操作。JSON 载荷沿用 128 KiB 上限。

### 14.2. 行列与区域工具

| 工具 | 专用参数与语义 |
|---|---|
| `sheets_sheet_info` | `include` 候选 merges/row_heights/col_widths/hidden_rows/hidden_cols/groups/frozen；可用 `cell_range` 限定范围，省略则查看整张子表布局 |
| `sheets_insert_dimensions` | `position` 为 1-based 行号或列字母；`count>=1`；始终在该位置之前插入；`inherit_style=before/after` 仅控制继承哪一侧的样式，默认 after |
| `sheets_delete_dimensions` | `ranges` 为 1–100 个同维度、不重叠的区间；坐标均按删除前布局填写；CLI 从高到低逆序合成一次批量删除，失败不保证回滚 |
| `sheets_move_dimensions` | `source_range` 行列区间、`position` 目标位置；须同维度且目标不在源区间内部；移动到目标之前 |
| `sheets_set_dimension_visibility` | `dimension_range`、`action=hide/unhide`；仅隐藏／显示行列，不删除数据、不改变子表标签可见性 |
| `sheets_group_dimensions` | `dimension_range`、`action=group/ungroup`、`depth>=1`；group 可用 `group_state=expand/fold`，默认 expand；ungroup 不接受 group_state |
| `sheets_freeze` | 必填 `rows>=0` 和 `columns>=0`，一次描述完整冻结状态；0 解除对应轴冻结 |
| `sheets_resize_dimensions` | `dimension=rows/columns`；统一模式为 `dimension_range+size`，差异模式为 `sizes` 映射，两者互斥；大小为正整数像素或 standard，auto 仅支持行；映射区间不可重叠 |
| `sheets_merge_cells` | `cell_range`、`action=merge/unmerge`；merge 可用 `merge_type=all/rows/columns`，默认 all；unmerge 不接受 merge_type |
| `sheets_copy_range` | `source_range`、`target_range`、可选 target_sheet_id；`paste_type=values/formulas/formats/all`，默认 all；行高列宽另设 |
| `sheets_move_range` | 剪切 source_range 到 target_range，可选 target_sheet_id；可能覆盖目标，源区域随之变化 |
| `sheets_fill_range` | `source_range` 为种子区域，`target_range` 为填充区域；`series_type=auto/linear/growth/date/copy`，默认 auto |
| `sheets_sort_range` | `cell_range` 须覆盖完整记录宽度；`sort_keys=[{"column":"C","ascending":false}]`，按数组顺序确定优先级；列字母须在范围内；`has_header=true` 跳过区域首行，默认 false |

结构改动前用 `sheets_sheet_info` 读取合并、尺寸、隐藏、分组和冻结状态，并用 `sheets_read` 检查附近值、公式与数据验证；操作后重新定位坐标并回读相关布局。插入后的样式继承不代表行高列宽也已匹配，尺寸需单独验证。

合并会仅保留各合并区域左上角内容，先确认其他位置无待保留内容；取消合并可一次覆盖包含多个合并区的大范围。排序前核对存储类型，带单位或货币符号的文本按字典序排序；不能用读值后整块覆盖模拟原生排序。复制／填充在源目标重叠时也不能盲目重复调用。

### 14.3. 查找与替换

| 工具 | 专用参数与行为 |
|---|---|
| `sheets_search_cells` | 必填非空 `find`；可选 cell_range，省略表示整张选定子表；`match_case`、`match_entire_cell`、`regex`、`include_formulas` 均默认 false；`max_matches` 默认 100、范围 1–5000；`offset>=0` 默认 0；支持 dry_run |
| `sheets_replace_cells` | 必填非空 `find` 和 `replacement`；空 replacement 删除命中文本；其余匹配参数同查找；省略 cell_range 会替换整张子表的匹配项；正则捕获组用 `$1`、`$2`；支持 dry_run |

查找结果的 `data` 包含 `matches`（address/value/sheet_id）、`total_matches`、`has_more`、`next_offset`；有后续结果时按游标继续。先查找核对目标，再预览／执行替换；执行后复查旧值残留并抽查新值。只有明确修改公式时才启用 `include_formulas`，写后另做公式校验。正则语法由 CLI 校验，替换不保证幂等，例如 `a → aa` 重发会继续改变结果。

### 14.4. 条件格式

| 工具 | 专用参数与行为 |
|---|---|
| `sheets_list_conditional_formats` | 可选 `rule_id` 过滤，否则返回选定子表全部规则及其配置 |
| `sheets_get_conditional_format_result` | `cell_range` 必填；`max_chars` 默认 25000、范围 1–500000；返回最终计算样式和区域／截断元数据，不包含值、公式、独立命中标记或 rule_id |
| `sheets_write_conditional_format` | `action=create/update`；`rule` 为完整规则；update 必须提供 rule_id，create 不接受 rule_id；支持 dry_run |
| `sheets_delete_conditional_format` | 必填 rule_id，执行要求 confirmed=true；dry_run 仅预览；删除后回查规则列表确认 ID 已消失 |

`rule` 公共字段为 `rule_type`、非空 `ranges`、必填 `style`（可为 `{}`）及可选 `has_ref`。`style` 支持背景色 `back_color`、字体色 `fore_color`、`font=bold/italic/bold italic`、`text_decoration=none/underline/strikethrough/underline_strikethrough`。每种规则的 `attrs` 使用独立 Schema，不接受其他规则的字段。

| rule_type | attrs |
|---|---|
| duplicateValues / uniqueValues / containsBlanks / notContainsBlanks | 无需 attrs；重复值规则按单列判重 |
| cellIs | `[{compare_type,value}]`；比较候选 equal/notEqual/greaterThan/greaterThanOrEqual/lessThan/lessThanOrEqual/between/notBetween；value 为字符串，区间用逗号分隔两个值 |
| containsText | `[{compare_type,text}]`；比较候选 beginsWith/endsWith/containsText/notContains/is |
| timePeriod | `[{operator,time_period}]`；operator 为 before/is/after；相对日期候选在 Schema 中列出 |
| dataBar | `[{color,value_type,value?,gradient?,hide_value?}]` |
| colorScale | 2 或 3 个 `{color,value_type,value?}`，按最小／中间／最大顺序 |
| rank | `[{is_bottom,value_type,value?}]`，value_type 为 percent/sort |
| aboveAverage | `[{operator}]`，比较候选 greaterThan/greaterThanOrEqual/lessThan/lessThanOrEqual |
| expression | `[{formula:["=$B2>100"]}]`，公式列表非空 |
| iconSet | `[{icon_type,value_type,operator,value?,hide_value?,reverse_icons?}]`，图标集候选完整公开，包含进度圆环 5CirclesRatio |

阈值类型 `value_type`（rank 除外）候选为 minValue/maxValue/num/percentile/percent/formula/auto，具体阈值由 CLI／远端按规则校验。`update` 完整替换整条规则，先读取并保留所有需要的配置。创建不幂等，结果不确定时先查现有规则。

创建／更新后，先用规则列表核对定义，再用计算结果工具抽查应命中／不应命中的单元格及空值、边界值。日期比较公式须排除空白，例如 `=AND($E2<>"",$E2<TODAY())`；“标红”使用 back_color，“字体红”使用 fore_color。
