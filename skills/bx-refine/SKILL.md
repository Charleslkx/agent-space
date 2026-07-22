---
name: bx
description: USE FOR current web search, documentation lookup, debugging, news, images, videos, and source ranking. Use the connected Brave Search MCP tool `brave_search_cli` for every bx request; do not install or configure a local bx binary.
---

# bx — Brave Search MCP

所有 bx 调用都通过 MCP 工具 `brave_search_cli` 完成。

- `args` 是 `bx` 后的参数数组，不包含 `bx` 本身，也不使用 shell 字符串。
- 需要 stdin 时传 `stdin` 字符串，例如 `--goggles @-`。
- 返回值保留 bx 原始 `stdout`、`stderr`、`exit_code` 和 `timed_out`。先检查 `exit_code` 与 `timed_out`，成功时再解析 stdout JSON。
- 服务端持有 Brave API Key；不要安装 bx、设置 `BRAVE_SEARCH_API_KEY` 或调用 `bx config`。

## 默认选择

当前部署套餐于 2026-07-22 实测仅开放 Search：`web`、`news`、`images`、`videos`。默认使用 `web`；不要使用不在套餐内的命令。

| 目标 | `args` 示例 |
|---|---|
| 文档、错误、代码模式 | `["web", "Python asyncio gather vs wait", "--count", "5"]` |
| 传统网页结果 | `["web", "site:docs.rs axum middleware", "--count", "5"]` |
| 论坛讨论 | `["web", "Rust async runtime", "--result-filter", "discussions"]` |
| 时效新闻 | `["news", "npm security advisory", "--freshness", "pd"]` |
| 图片、视频 | `["images", "microservice diagram"]`、`["videos", "Rust async tutorial"]` |

已实测不可用：`context`、`answers`、`places`、`suggest`、`spellcheck`；它们会返回 `OPTION_NOT_IN_PLAN`。套餐变更后再重新测试并更新本表。

## 输入与输出

`web` 输出 `.web.results[]`，并可能有 `.news.results[]`、`.videos.results[]` 和 `.discussions.results[]`。

## Token 与来源控制

对 `web` 使用较小且明确的结果数量：

```text
args: ["web", "axum middleware authentication", "--count", "5"]
```

优先用 `--include-site`、`--exclude-site` 或内联 `--goggles` 控制来源：

```text
args: ["web", "Python asyncio patterns", "--include-site", "docs.python.org", "--include-site", "peps.python.org"]

args: ["web", "axum middleware tower", "--goggles", "$boost=5,site=docs.rs\n$boost=3,site=github.com\n$discard,site=example.com"]
```

要把 Goggles 规则通过 stdin 传入时，使用 `@-`：

```text
args: ["web", "axum middleware", "--goggles", "@-"]
stdin: "$boost=5,site=docs.rs\n$boost=3,site=github.com"
```

## MCP 边界

以下 bx 输入在此 MCP 中被拒绝：

- `config` 子命令。
- `--api-key`、`--config`、`--base-url`。
- 本地 `--goggles @文件`。

不要重试这些输入。需要配置变更时修改远程 MCP 的部署环境；需要复用 Goggles 时使用内联规则、`@-` 加 stdin，或 HTTPS 托管的规则 URL。

单次请求最多 128 个参数，stdin 1 MiB，stdout/stderr 各 10 MiB，运行上限 180 秒。

## 失败处理

| 返回 | 处理 |
|---|---|
| `timed_out: true` | 缩小查询或减少结果数后重试一次 |
| `exit_code: 1` 或 `2` | 修正查询、参数或命令组合 |
| `exit_code: 3` | 服务端 Brave 凭据或套餐问题；报告 stderr，不要尝试修改 API Key |
| `exit_code: 4` | 限流；等待后退避重试 |
| `exit_code: 5` | 网络或 Brave 服务端错误；退避重试一次 |

`brave_search_cli` 是只读但非幂等工具：重复调用会消耗 Brave API 配额。只在需要当前信息时调用，引用结果中的原始 URL。
