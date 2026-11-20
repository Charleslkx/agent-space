# Lark Doc MCP

通过 GitHub OAuth 提供飞书文档、Markdown、画板、电子表格与多维表格操作。飞书凭据保存在服务账户状态目录。

文档与 Wiki 的评论支持只读查询，可分页读取评论正文、回复和 Docx 定位信息。用法、权限和验收步骤见[配置文档](CONFIG.md#10-mcp-工具)。

## 1. 部署入口

Ubuntu／Debian 统一按 [Linux 部署运行手册](../DEPLOYMENT.md)执行。从已审核的分发包解出完整 `mcp/` 目录，保留共享脚本、隐藏示例文件和锁文件。从 `/opt/mcp/lark-doc` 执行命令。

| 项目 | 值 |
|---|---|
| 服务名 | `lark-doc` |
| 公开端点 | `https://实际域名/mcp` |
| 环境文件 | `/etc/lark-doc.env` |
| 配置前缀 | `LARK_MCP` |
| 配置详情 | [CONFIG.md](CONFIG.md) |

完整流程包含独立 GitHub OAuth App、服务启动、Nginx 模板渲染、TLS 证书、OAuth 和真实工具调用验收。仅启动进程不能视为部署完成。

## 2. 从 lark-markdown 迁移

重命名改变了持久标识：OAuth `client_id`、MCP 名称、systemd 单元 `lark-doc-mcp`、服务账户、`/var/lib/lark-doc`、`/etc/lark-doc.env`、重启确认词 `RESTART_LARK_DOC_MCP` 与 WorkBuddy 回调地址。旧实例按顺序迁移：

1. 停止并禁用 `lark-markdown-mcp`，释放 8765 端口。
2. 将 `/var/lib/lark-markdown` 移到 `/var/lib/lark-doc`，`/etc/lark-markdown.env` 移到 `/etc/lark-doc.env`，属主改为 `lark-doc`；保持存储密钥与持久数据配套。
3. 按[运行手册](../DEPLOYMENT.md)创建 `lark-doc` 账户与单元，客户端重新添加连接并重新授权。

## 3. 运维入口

日志、更新、备份和回滚见运行手册第 7 节。修改凭据后重启 systemd 服务。首次安装自动选择官方最新稳定 CLI／应用依赖；已有实例沿用本机记录，只有明确升级才重新解析。

## 4. 开发环境

```bash
bash ../scripts/check-python.sh --setup
python3 ../scripts/prepare-python.py lark-doc --native
```
