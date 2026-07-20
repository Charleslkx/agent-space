---
name: lark-markdown
description: "使用远程 Lark-Markdown MCP 阅读、创建和编辑飞书 Docx/Wiki 文档，保留 Markdown 格式、公式、超链接、图片和飞书原生块；也支持按明确要求部署或配置该 MCP。用户提到飞书文章阅读、修改、重写、批量处理、本地 Markdown 发布或 Lark-Markdown MCP 配置时必须使用。"
---

# Lark-Markdown

默认把本 skill 当作远程 MCP 的日常使用指南。只有用户明确要求安装、部署、启动、迁移、切换域名或配置 Lark-Markdown MCP 时，才进入服务器配置流程。

## 路由

### 日常文档模式（默认）

用户要求阅读、总结、创建、编辑、追加、重写、插图或修改飞书文档时：

- 只调用已连接的 `Lark-Markdown` MCP 工具。
- 不运行 `uv`、`python scripts/mcp_server.py`、`lark-cli`、Nginx、systemd 或 OAuth 服务端配置。
- 不因为看到了本项目目录就启动本地 MCP。
- 本机没有 `lark-cli` 不影响远程 MCP 使用；`lark-cli` 位于服务器端。

### 服务器配置模式（仅显式触发）

只有用户明确要求配置、安装、部署、启动、升级、迁移或修改 MCP 服务器时，读取 [`README.md`](README.md)。日常文档请求不得读取或执行服务器配置步骤。

`scripts/manage_secret_key.py` 仅供用户本人在交互式终端手动执行。Agent 即使正在协助部署，也只能给出命令，不得运行、读取其输出或展示现有密钥。

### 本地 Markdown 目录发布模式（按需）

用户要求把本地 Markdown 目录发布、增量同步或反向拉取到飞书时，额外读取 [`references/markdown-publish.md`](references/markdown-publish.md)。普通单篇文档编辑不要加载该文件。

## 连接与认证

当前已知远程配置名为 `lark-markdown`，域名是 `https://lark-markdown.nexuszone.link/mcp`。

按当前操作直接调用所需 MCP 工具；不要为确认工具清单而枚举工具、读取文档或探测所有能力。

- 工具可调用时直接使用，不执行服务器安装。
- 工具缺失或连接失败时，报告“远程 Lark-Markdown MCP 未连接”，不要自行创建本地服务。
- 首次使用、认证状态不明或文档工具返回认证错误时调用 `check_lark_cli`。
- `user_status=ready` 且 `verified=true` 表示服务器端飞书用户认证可用。
- `update_notice` 只表示存在可选更新；可以转告用户手动运行 `lark-cli update`，不得自动升级或阻断当前操作。
- 用户明确要求重启服务时才可调用 `schedule_mcp_restart`；`confirmation` 必须为 `RESTART_LARK_MARKDOWN_MCP`，延迟范围为 5–300 秒。工具会在当前调用返回后重启远端 MCP。
- 只有认证缺失或过期时才调用 `begin_lark_auth`，把授权 URL、device code 或二维码交给用户；用户确认完成页面授权后再调用 `complete_lark_auth`。
- OAuth 登录恢复属于远程连接认证，不等于服务器部署；不要因此进入服务器配置模式。

## 文档操作

### 阅读

1. 调用 `batch_pull`，单篇也传一项数组。
2. 常规阅读用 `doc_format=markdown`、`detail=simple`。
3. 涉及飞书原生块、块级格式或精确结构时，再用 `doc_format=xml`、`detail=full` 回读。
4. 基于实际回读内容回答，不根据标题、URL 或旧副本猜测。

### 编辑

- 用户提供完整替换正文、明确要求覆盖时，直接使用 `batch_push(mode=overwrite)`，不预读全文。
- 局部、唯一文本修改且已提供唯一旧文本时，直接使用 `point_update`；旧文本不唯一、未提供或需保留未知上下文时，才先用 Markdown `detail=simple` 读取。
- 已知包含飞书原生块、或本次操作涉及块级格式/公式/链接保真时，先用 XML `detail=full` 读取。
- 局部、唯一文本修改：使用 `point_update`。
- 删除唯一文本：`point_update` 的 `replacement` 传空字符串。
- 整篇重写或明确覆盖：使用 `batch_push` 的 `mode=overwrite`。
- 仅当用户明确要求追加时使用 `mode=append`。
- 多篇文档可使用 `batch_push`，单批不超过 100 项。
- `batch_push` 和 `batch_pull` 默认通过远程 MCP 以 4 路并发执行，可传 `concurrency=1–8`。仅同一页面的顺序操作、父节点尚未创建的子节点、或服务端限流时设为 1；并发请求失败时，回读该批目标确认已写入项。

写入成功且非 `partial_success` 时默认不回读全文。仅在 `partial_success`、报错、格式保真操作（原生块、公式、媒体、链接）、用户要求验收，或写入结果与预期不一致时回读；`partial_success` 必须靠回读判定，不能视为完成。

### 创建与媒体

- 创建 Wiki 空间：使用 `create_wiki_space(name, description?)`。它创建独立 Wiki 空间，不创建页面。
- 创建 Wiki 页面：使用 `create_wiki_node(title, parent_node_token?, space_id?)`。根页面传 `space_id`；子页面传 `parent_node_token`；两者至少提供一个。该工具创建带空白 Docx 的 Wiki 节点。
- 创建普通 Drive Docx：使用 `create_document(content, parent_token?)`。`parent_token` 是 Drive 文件夹 token 时创建到该文件夹；已有 Wiki 的层级页面优先使用 `create_wiki_node`，不要把它当作创建 Wiki 节点的替代方案。
- 创建成功后直接使用返回的文档 URL/token；只有返回信息缺失、创建结果异常或用户要求验收时才回读。
- 本地图片或附件使用 `insert_media`，传纯文件名和 base64；需要原位插入时提供唯一 `selection`，必要时设置 `before=true`。
- 不把图片默认附加到文末。媒体写入成功后不回读全文；只有选择标记不唯一、写入结果异常或用户要求位置验收时回读目标文档。

### 画板

- 修改前用 `whiteboard_query` 获取现状。
- 使用 `whiteboard_update` 写入 Mermaid、PlantUML 或 raw 节点。
- 写入后再次查询；只有回读结果符合预期才报告完成。

## 格式和链接规则

- 保留标题层级、段落、粗体、斜体、删除线、代码、引用、分隔线、有序/无序列表、嵌套列表和 GFM 表格。
- 保留行内公式；展示公式优先保持为独立公式块。仅在本次写入涉及公式、公式发生转换或用户要求格式验收时，同时回读 Markdown 和 XML。
- 保留 HTTP(S) 超链接的文字和目标，不把可点击链接降级为裸文本。
- 本地目录发布时，GFM 脚注 `[^id]` 由最终渲染脚本确定性改写为 `[id]` 和飞书引用块 `> [id] 来源`；不要求 AI 逐篇处理。
- 飞书文档 URL 不支持由 Markdown 稳定构造标题锚点；不要伪造 `#标题` 跳转。
- 编辑含飞书原生块的文档时，先用 XML `detail=full` 读取；无法通过 Markdown 保真的块不要整篇覆盖。
- URL 预览、按钮、提醒等已知可能降级的内容仅在本次写入触及这些块时回读验证，并明确报告降级。
- Bitable、同步块、OKR 等不能可靠新建的资源块只保留或移动已有块，不伪造 token 或 ID。

## 写入边界

- 未取得目标文档 URL/token 时不执行写操作。
- 用户只要求审阅、解释或总结时不修改文档。
- 删除、整篇覆盖和批量写入以用户当前请求为准，不扩大目标集合。
- 正文单份上限 10 MiB，媒体解码后上限 20 MiB，CLI 调用最长 60 秒；超限时拆分内容，不绕过限制。
- MCP 返回的操作名、失败索引、文档标识、退出码和消息应原样保留在错误报告中。

## 批量处理与 token 控制

- 本地 Markdown 目录操作先运行 `scripts/prepare_publish.py`、`plan_incremental.py`、`center_display_math.py` 和 `build_folder_indexes.py`，从 manifest、计划和渲染稿驱动请求；不要逐篇读取正文后再由模型手工重组内容。
- 本地脚本只做确定性扫描、转换、计划和状态提交；所有飞书读写统一调用已连接的远程 MCP，绝不改用本机 `lark-cli`。
- 先用一篇格式敏感页面验证，再将互不依赖的正文作为一批交给 `batch_push`；仅回读失败、部分成功或格式敏感项。
