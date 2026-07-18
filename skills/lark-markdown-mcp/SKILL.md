---
name: lark-markdown-mcp
version: 0.7.0
description: "将本地 Obsidian Markdown 目录发布到飞书 Docx 或 Wiki 节点。当用户要求上传、迁移、同步本地 Markdown/Obsidian 知识库到飞书，并要求保留公式、图片、相互引用或把相对链接改为飞书文档链接时，必须使用本 skill。"
metadata:
  requires:
    bins: ["lark-cli", "python3"]
---

# 发布 Obsidian Markdown 到飞书

> 本 skill 自包含，不依赖其他 Codex skill。执行写操作前用 `lark-cli skills read lark-doc` 及其引用路径读取 CLI 随版本附带的指南；认证规则用 `lark-cli skills read lark-shared`。写入使用 `--as user`，文件路径必须相对当前工作目录。

## 输入与边界

需要：本地目录、目标 Wiki 节点 token（或目标文件夹 token）、是否允许创建新文档。未提供目标 token 时只做预检，不执行写入。

不修改源 Markdown。所有转换写入隐藏工作目录 `.lark_publish/`；发布状态写入 `.lark_publish/state.json`，并将 `.lark_publish/` 加入 `.gitignore`。每次运行结束（成功或失败）都删除一次性产物，只保留 `state.json`、`url-map.json` 和 `report.json`。

跨文档引用可能形成环，不能按拓扑序逐篇导入。固定采用两阶段：**先为全部 Markdown 和本地文件夹创建空 Docx 并取得 URL，再回写正文**。这是唯一能保证循环引用正常的顺序。

本地文件夹不创建飞书“文件夹”节点：每个文件夹对应一个 Docx 页面。该页面列出该文件夹内（含子文件夹）的所有本地 Markdown 文档，并将链接改写为其飞书 URL；所有 Markdown 页面和文件夹页面都在同一 Wiki 层级中建立父子关系。

飞书文档 URL 不提供可由 Markdown 稳定构造的标题锚点；`file.md#标题` 转换成目标文档 URL，保留链接文字，并在报告中列出被降级的章节跳转。

## 增量发布

首次成功发布后，将每个文档的源文件 SHA-256、飞书 URL、doc token 与 revision 写入 `state.json`。后续先生成最新 `manifest.json`，再计算最小写入集合：

```bash
python3 scripts/plan_incremental.py \
  --manifest .lark_publish/manifest.json --state .lark_publish/state.json \
  --out .lark_publish/incremental-plan.json
```

- 只对 `write_set` 执行 `docs +update`；未变更文件不读取、不覆盖。
- 新增文档先创建并补充 URL 映射；其已有引用方也进入 `write_set`，以写入新 URL。
- 本地删除只列在 `deleted_local`，默认不删除远端节点。删除远端必须单独取得用户确认。

## 反向拉取

反向拉取不是推送的镜像操作：飞书 Docx 不保存原始 Obsidian 路径、Wiki 链接语法、图片原文件名和部分排版元数据。只对本 skill 创建并已记录在 `state.json` 的受管文档执行可逆拉取；其他远端节点先列为 `new_remote`，不自动写入本地。

1. 对每个受管 doc token 执行 `docs +fetch --doc-format markdown`，将正文和 `revision_id` 写入 `.lark_publish/remote/` 与 `remote-index.json`。
2. 运行冲突规划：

```bash
python3 scripts/plan_pull.py \
  --state .lark_publish/state.json --remote-index .lark_publish/remote-index.json \
  --local-root knowledge-base/example --out .lark_publish/pull-plan.json
```

3. 仅将 `pull` 项转换到临时目录，再生成 diff；`conflicts`、`new_remote`、`missing_remote` 一律停下并报告。未经用户确认，绝不覆盖本地文件或删除本地文件。
4. 拉取转换时，把 `url-map.json` 中的受管飞书 URL 反写为相对 `.md` 链接；居中 `<latex>` 段落反写为 `$$...$$`。图片和附件仅在 `state.json` 有原始本地路径与远端资源 token 对应关系时下载并替换，否则保留远端链接并报告。

## 1 预检

先检查二进制、版本和用户登录态；任一步失败都停止，不创建中间文件：

```bash
command -v lark-cli
lark-cli --version
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1 \
  lark-cli auth status --json --verify
```

`ok`、`verified` 或 `identities.user.status` 显示不可用时，按 `lark-shared` 修复配置或重新授权后再继续。

```bash
python3 scripts/prepare_publish.py \
  knowledge-base/math/ab-test --out .lark_publish
```

检查 `.lark_publish/manifest.json`：

- `errors` 必须为空；控制字符、无法解析的本地图片或链接先修复源文件后重跑。
- 检查 `documents`、`edges`、`images`；记录入边/出边和所有带 `fragment` 的链接。
- 报告重复标题；飞书同一节点下标题重复时先要求用户改名或确认。

## 2 创建文档并生成 URL 映射

目标为 Wiki 节点时传 `--parent-token <wiki_node_token>`；目标为普通 Drive 文件夹时传文件夹 token。

先按本地目录树创建每个文件夹的空 Docx 页面，再按 `manifest.json` 的 `documents` 顺序创建 Markdown 的空 Docx 页面；立即将每个返回的 `document.url` 写入 `.lark_publish/url-map.json`。Markdown 置于其父文件夹页面下，子文件夹页面置于父文件夹页面下。

```json
{
  "relative/path.md": "https://example.feishu.cn/docx/docx_token"
}
```

示意命令：

```bash
lark-cli docs +create --api-version v2 --as user --parent-token "$PARENT_TOKEN" \
  --content '<title>文档标题</title>' --format json
```

创建前先 dry-run；创建成功后才能继续。失败时停止，保留已写入的 `url-map.json` 以便恢复，不要重建已存在的文档。

## 3 生成已改写正文

```bash
python3 scripts/prepare_publish.py \
  knowledge-base/math/ab-test --out .lark_publish --url-map .lark_publish/url-map.json
```

该步骤将相对 `.md` 链接改为映射中的飞书 URL。随后把独立的 `$$...$$` 转为居中的飞书公式段落；行内 `$...$` 保持不变：

```bash
python3 scripts/center_display_math.py \
  .lark_publish/markdown .lark_publish/markdown-rendered
```

只上传 `.lark_publish/markdown-rendered/`，绝不覆盖本地源文件。

## 4 写入正文、文件夹索引与图片

对每个映射条目执行：

```bash
lark-cli docs +update --api-version v2 --as user --doc "$DOC_URL" --command overwrite \
  --doc-format markdown --content "@.lark_publish/markdown-rendered/$RELATIVE_PATH" --format json
```

先抽样读取 1 个含表格/公式/链接的文档验证格式，再写入其余文档。写入完成后用 `docs +fetch --doc-format markdown` 复核。

在全部 Markdown 页面 URL 已确定后，为每个文件夹生成索引页；索引页列出该文件夹下所有 Markdown 页面（含递归子文件夹）的飞书链接：

`docs.json` 的键统一使用相对发布根目录的 Markdown 路径，不添加 `knowledge-base/` 等固定前缀。

```bash
python3 scripts/build_folder_indexes.py \
  --root knowledge-base/math --label math \
  --nodes .lark_publish/nodes.json --docs .lark_publish/docs.json \
  --out .lark_publish/folder-indexes
```

将每个 `.lark_publish/folder-indexes/*.md` 覆盖写入其对应文件夹 Docx。页面已存在时只重写索引页，绝不重建叶子 Markdown 页面，避免产生重复节点。

本地图片不能直接作为 Markdown 相对路径导入。对每个 `manifest.images` 条目：先在正文中保留唯一文本标记，使用 `docs +media-insert --selection-with-ellipsis <标记> --before --file <相对本地路径>` 插入图片，再用 `docs +update --command str_replace` 删除标记。不得把图片附加到文末；外部 `https` 图片可保留 Markdown 图片链接。

## 5 验收

逐篇确认：

- 远端文档数等于 `manifest.documents` 数量。
- 每个本地文件夹均有一个飞书 Docx 页面；其索引中的每个链接均指向已创建的 Markdown 页面。
- 抽取 XML 验证每个原 `$$...$$` 块都变为 `<p align="center"><latex>...</latex></p>`；公式在飞书页面渲染正确。
- `manifest.edges` 的每条源文档链接目标属于 `url-map.json`，远端 Markdown 中不再出现指向本地 `.md` 的链接。
- 每个本地图片都已插入到原标记位置。
- 将文档 URL、源 SHA-256、远端 revision 和错误写入 `.lark_publish/report.json`。

若返回 `partial_success`，必须 fetch 并执行本节验收；验收通过才可继续。遇到权限、scope、限流或验收失败时停止并报告具体文件；不要静默跳过。

### 5.1 能力矩阵

发布目标是完整保留 `lark-cli docs` 当前支持的 Markdown，并对 Obsidian 本地语义做确定性转换；不要声称支持未定义的“所有 Markdown 方言”。

| 输入能力 | 处理方式 |
|-|-|
| 段落、H1-H6、粗体、斜体、删除线、行内代码、代码块、引用、分隔线、链接、有序/无序及嵌套列表、GFM 表格、行内公式 | 原样交给 `docs +update --doc-format markdown`；内容开头唯一 H1 会成为飞书文档标题 |
| HTTP(S) 图片 | 保留 Markdown 图片 URL，由飞书下载 |
| 本地图片、Obsidian `![[image]]` | 标记后用 `docs +media-insert` 原位插入 |
| 相对 `.md` 链接、循环引用 | 两阶段 URL 映射后改写；标题锚点降级为文档 URL |
| `$$...$$` 展示公式 | 转换为居中的 `<latex>` 段落；不得转换代码块中的字面量 |
| 下划线、待办、高亮框、分栏、文字色/背景色、书签、@人/@文档 | 在 Markdown 中嵌入 `lark-doc-xml.md` 对应标签；需要 token/ID 的组件只有输入真实标识后才写入 |
| URL 预览、按钮、提醒 | CLI 文档列有 XML 标签，但 `lark-cli 1.0.56` 实测会降级为文本或丢弃；列入 `report.json` 的降级项，不宣称原生块保真 |
| 画板 | 简单图直接嵌入 `<whiteboard type="mermaid">`；拿到 `block_token` 后用 MCP `whiteboard_query` / `whiteboard_update` 或等价 `lark-cli whiteboard` 命令读写 |
| Sheet、任务、群聊卡片、Wiki 子页面列表 | 使用 XML 资源块并要求真实 token/ID；不伪造测试数据 |
| Bitable、同步块、OKR 等 CLI 标为不可创建的资源块 | 只保留或移动已有块，不从 Markdown 新建 |

验收时同时 fetch Markdown 与 XML：Markdown 回读检查文本语义，XML 回读检查飞书原生块类型。画板还要用 `whiteboard +query --output_as code` 验证可读，并至少执行一次更新后再次查询。

## 6 清理

验收或异常处理结束后都运行：

```bash
python3 scripts/cleanup_workspace.py \
  --workdir .lark_publish
```

该命令只保留增量发布和恢复所需的 `state.json`、`url-map.json`、`report.json`；其余 manifest、渲染稿、拉取副本、索引和计划文件全部删除。不得删除源 Markdown。

## 7 FastMCP 服务

`scripts/mcp_server.py` 提供十个工具：`check_lark_cli`、`begin_lark_auth`、`complete_lark_auth`、`batch_pull`、`batch_push`、`point_update`、`create_document`、`insert_media`、`whiteboard_query`、`whiteboard_update`。`begin_lark_auth` 按需生成 docs/drive 的一次性用户授权 URL、device code 与二维码，`complete_lark_auth` 在用户已完成页面授权后提交 device code；其余写入或读取工具（健康检查除外）每次调用前都会检查 `lark-cli` 与 user 登录态；需要文件载荷的操作使用 `.lark_publish/.run-*` 隐藏目录，并在成功或异常时删除。清理失败会显式报错，不会静默遗留正文。

该目录是完整 uv 项目。复制目录后运行 `uv sync --frozen`；依赖版本由 `uv.lock` 固定。部署细节见 [`README.md`](README.md)。

```bash
uv sync --frozen
uv run python scripts/mcp_server.py
```

本地 HTTP 模式只绑定回环地址，默认入口为 `http://127.0.0.1:8765/mcp`：

```bash
uv run python scripts/mcp_server.py \
  --transport http --host 127.0.0.1 --port 8765
```

公网优先按 README 使用反向代理终止 TLS，MCP 仍绑定 `127.0.0.1`。ChatGPT 模式使用 `LARK_MCP_AUTH_MODE=github`：FastMCP 提供 OAuth 2.1、PKCE、CIMD/DCR 和发现路由，`LARK_MCP_GITHUB_USER` 限制唯一允许账户。静态 Bearer Token 仅用于 Codex 等支持自定义 Token 的客户端，不适用于 ChatGPT。非回环地址缺少认证、证书或私钥时拒绝启动。

```bash
export LARK_MCP_AUTH_MODE=github
export LARK_MCP_BASE_URL=https://mcp.example.com
export LARK_MCP_GITHUB_CLIENT_ID=...
export LARK_MCP_GITHUB_CLIENT_SECRET=...
export LARK_MCP_GITHUB_USER=your-login
uv run python scripts/mcp_server.py \
  --transport http --host 0.0.0.0 --port 8765 \
  --tls-cert /etc/letsencrypt/live/mcp.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/mcp.example.com/privkey.pem
```

用一个明确可覆盖写入的测试 Docx 运行 Markdown、原生块和画板回读测试：

```bash
uv run python scripts/test_live_capabilities.py \
  --url http://127.0.0.1:8765/mcp --doc "$TEST_DOC_URL"
```

- `batch_pull(documents, doc_format, detail)`：批量读取 Docx/Wiki，返回 Markdown 或 XML 正文和 revision；验收原生块时用 `detail=full`，不留下本地副本。
- `batch_push(documents)`：批量 `overwrite` 或 `append`；每项传 `doc`、`content`，可选 `mode`、`doc_format`。
- `point_update(doc, pattern, replacement, doc_format)`：使用 `str_replace` 精确替换；`replacement` 为空时删除匹配内容。
- `create_document(content, doc_format, parent_token)`：在个人空间、Drive 文件夹或 Wiki 节点下创建文档。
- `insert_media(doc, filename, content_base64, media_type, selection, before)`：从 base64 插入图片或附件，调用后删除载荷。
- `whiteboard_query(whiteboard_token, output_as)`：读取画板代码或原生节点，`output_as` 为 `code` / `raw`。
- `whiteboard_update(whiteboard_token, source, input_format, overwrite)`：用 Mermaid、PlantUML 或 raw 节点更新已有画板，载荷调用后删除。

单批最多 100 项，单份正文最多 10 MiB，媒体解码后最多 20 MiB；每次 `lark-cli` 调用最长 60 秒。批量失败错误包含操作名、失败索引、文档标识、已完成数量、CLI 退出码与消息。
