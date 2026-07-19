# 本地 Obsidian 目录发布

仅当用户要求发布、迁移、增量同步或反向拉取本地 Markdown/Obsidian 目录时读取本文件。日常飞书文档阅读和编辑使用 `SKILL.md` 的远程 MCP 工作流，不运行这里的本地脚本。

本地转换只需要 Python 和本目录下的 `scripts/`；飞书读取和写入统一调用已连接的远程 Lark-Markdown MCP，不要求本机安装 `lark-cli`。

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

- 只把 `write_set` 交给 MCP `batch_push`；未变更文件不读取、不覆盖。
- 新增文档先创建并补充 URL 映射；其已有引用方也进入 `write_set`，以写入新 URL。
- 本地删除只列在 `deleted_local`，默认不删除远端节点。删除远端必须单独取得用户确认。

## 反向拉取

反向拉取规划不是推送的镜像操作：飞书 Docx 不保存原始 Obsidian 路径、Wiki 链接语法、图片原文件名和部分排版元数据。只对本 skill 创建并已记录在 `state.json` 的受管文档生成安全计划；其他远端节点先列为 `new_remote`，不自动写入本地。

1. 用 MCP `batch_pull` 读取受管文档的 Markdown 与 `revision_id`，将结果写入 `.lark_publish/remote/` 与 `remote-index.json`。
2. 运行冲突规划：

```bash
python3 scripts/plan_pull.py \
  --state .lark_publish/state.json --remote-index .lark_publish/remote-index.json \
  --local-root knowledge-base/example --out .lark_publish/pull-plan.json
```

3. 仅将 `pull` 项转换到临时目录，再生成 diff；`conflicts`、`new_remote`、`missing_remote` 一律停下并报告。未经用户确认，绝不覆盖本地文件或删除本地文件。
4. 拉取转换时，把 `url-map.json` 中的受管飞书 URL 反写为相对 `.md` 链接；居中 `<latex>` 段落反写为 `$$...$$`。图片和附件仅在 `state.json` 有原始本地路径与远端资源 token 对应关系时下载并替换，否则保留远端链接并报告。

## 1 预检

先调用 MCP `check_lark_cli`。工具缺失、连接失败或 `verified=false` 时停止，不创建中间文件；返回 `update_notice` 时只提示可手动升级，不影响发布。

```bash
python3 scripts/prepare_publish.py \
  knowledge-base/math/ab-test --out .lark_publish
```

检查 `.lark_publish/manifest.json`：

- `errors` 必须为空；控制字符、无法解析的本地图片或链接先修复源文件后重跑。
- 检查 `documents`、`edges`、`images`；记录入边/出边和所有带 `fragment` 的链接。
- 报告重复标题；飞书同一节点下标题重复时先要求用户改名或确认。

## 2 创建文档并生成 URL 映射

目标为 Wiki 时使用 `create_wiki_node`：根页面传 `space_id`，子页面传 `parent_node_token`。目标为普通 Drive 文件夹时使用 `create_document` 并传文件夹 token。

先按本地目录树创建每个文件夹的空白 Wiki Docx 节点，再按 `manifest.json` 的 `documents` 顺序创建 Markdown 的 Wiki Docx 节点；立即将每个返回的文档 URL 写入 `.lark_publish/url-map.json`。Markdown 置于其父文件夹节点下，子文件夹节点置于父文件夹节点下。

```json
{
  "relative/path.md": "https://example.feishu.cn/docx/docx_token"
}
```

发布到 Wiki 时使用 MCP `create_wiki_node`，根节点传 `space_id`、子节点传 `parent_node_token`；发布到 Drive 文件夹时使用 `create_document` 并传 `parent_token`。每次成功后立即记录返回 URL；不要等整批结束后才保存映射。

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

将渲染稿读入后交给 MCP `batch_push`，使用 `mode=overwrite` 和 `doc_format=markdown`。单批不超过 100 项。

先抽样读取 1 个含表格/公式/链接的文档验证格式，再写入其余文档。写入完成后用 MCP `batch_pull` 复核。

在全部 Markdown 页面 URL 已确定后，为每个文件夹生成索引页；索引页列出该文件夹下所有 Markdown 页面（含递归子文件夹）的飞书链接：

`docs.json` 的键统一使用相对发布根目录的 Markdown 路径，不添加 `knowledge-base/` 等固定前缀。

```bash
python3 scripts/build_folder_indexes.py \
  --root knowledge-base/math --label math \
  --nodes .lark_publish/nodes.json --docs .lark_publish/docs.json \
  --out .lark_publish/folder-indexes
```

将每个 `.lark_publish/folder-indexes/*.md` 覆盖写入其对应文件夹 Docx。页面已存在时只重写索引页，绝不重建叶子 Markdown 页面，避免产生重复节点。

本地图片不能直接作为 Markdown 相对路径导入。对每个 `manifest.images` 条目：先在正文中保留唯一文本标记，读取文件并用 MCP `insert_media` 原位插入，再用 `point_update` 删除标记。不得把图片附加到文末；外部 `https` 图片可保留 Markdown 图片链接。

## 5 验收

逐篇确认：

- 远端文档数等于 `manifest.documents` 数量。
- 每个本地文件夹均有一个飞书 Docx 页面；其索引中的每个链接均指向已创建的 Markdown 页面。
- 抽取 XML 验证每个原 `$$...$$` 块都变为 `<p align="center"><latex>...</latex></p>`；公式在飞书页面渲染正确。
- `manifest.edges` 的每条源文档链接目标属于 `url-map.json`，远端 Markdown 中不再出现指向本地 `.md` 的链接。
- 每个本地图片都已插入到原标记位置。
- 再次调用 `batch_pull`，把每篇的 URL、doc token 和 `revision_id` 写入 `.lark_publish/remote-index.json`。

验收通过后原子提交状态：

```bash
python3 scripts/commit_publish_state.py --workdir .lark_publish
```

该命令只有在 manifest、URL 映射和远端 revision 完整一致时才更新 `state.json`；失败只写 `report.json`，不覆盖上次成功状态。

若返回 `partial_success`，必须 fetch 并执行本节验收；验收通过才可继续。遇到权限、scope、限流或验收失败时停止并报告具体文件；不要静默跳过。

### 5.1 能力矩阵

发布目标是完整保留当前飞书文档转换层支持的 Markdown，并对 Obsidian 本地语义做确定性转换；不要声称支持未定义的“所有 Markdown 方言”。

| 输入能力 | 处理方式 |
|-|-|
| 段落、H1-H6、粗体、斜体、删除线、行内代码、代码块、引用、分隔线、链接、有序/无序及嵌套列表、GFM 表格、行内公式 | 原样交给 MCP `batch_push` 的 Markdown 模式；内容开头唯一 H1 会成为飞书文档标题 |
| HTTP(S) 图片 | 保留 Markdown 图片 URL，由飞书下载 |
| 本地图片、Obsidian `![[image]]` | 标记后用 MCP `insert_media` 原位插入 |
| 相对 `.md` 链接、循环引用 | 两阶段 URL 映射后改写；标题锚点降级为文档 URL |
| `$$...$$` 展示公式 | 转换为居中的 `<latex>` 段落；不得转换代码块中的字面量 |
| 下划线、待办、高亮框、分栏、文字色/背景色、书签、@人/@文档 | 在 Markdown 中嵌入 `lark-doc-xml.md` 对应标签；需要 token/ID 的组件只有输入真实标识后才写入 |
| URL 预览、按钮、提醒 | 不同 CLI/飞书版本可能降级为文本或丢弃；以实时回读为准，在结果中报告降级，不宣称原生块保真 |
| 画板 | 简单图直接嵌入 `<whiteboard type="mermaid">`；拿到 `block_token` 后用 MCP `whiteboard_query` / `whiteboard_update` 读写 |
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
