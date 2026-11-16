# MCP 服务

部署和开发前先按[运行手册](DEPLOYMENT.md#21-python-与非默认工具预检)验证 Python、venv 与 pip；uv 由准备脚本隔离管理。

首次配置时动态获取官方当时的最新稳定应用版本；已有安装复用当前版本，明确升级时重新查询。运行平台保留兼容范围。

五个远程 MCP 的 Ubuntu／Debian 部署统一从 [DEPLOYMENT.md](DEPLOYMENT.md) 开始。复制完整本目录到 `/opt/mcp/`，共享脚本随服务一起保留。

| 服务 | 配置参考 |
|---|---|
| [Brave Search](brave-search-mcp/README.md) | [配置](brave-search-mcp/CONFIGURATION.md) |
| [Firecrawl](firecrawl-mcp/README.md) | [配置](firecrawl-mcp/CONFIGURATION.md) |
| [Exa](exa-mcp/README.md) | [配置](exa-mcp/CONFIGURATION.md) |
| [Lark CLI](lark-cli-mcp/README.md) | [配置](lark-cli-mcp/CONFIGURATION.md) |
| [Lark Markdown](lark-markdown/README.md) | [配置](lark-markdown/CONFIG.md) |

部署 Agent 按运行手册收集输入、执行安装、完成 OAuth 和业务验收；缺少凭据或浏览器授权时记录未完成项，保留已有配置。客户端 Agent 的工具调用契约继续放在各服务现有 `AGENTS.md` 中。
