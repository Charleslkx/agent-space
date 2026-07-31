# 使用指南

## 1. 调用 `lark_cli`

`args` 是 `lark-cli` 后面的参数数组，不含二进制名，不需要 shell 引号：

```json
{"args":["calendar","+agenda","--as","user"]}
```

读取 schema：

```json
{"args":["schema","calendar.calendar.event.list","--format","json"]}
```

通用 OpenAPI 调用：

```json
{"args":["api","GET","/open-apis/contact/v3/users","--params","{\"page_size\":20}","--as","user"]}
```

需要 stdin 时用 `@-`：

```json
{"args":["api","POST","/open-apis/example","--data","@-","--as","user"],"stdin":"{\"name\":\"demo\"}"}
```

响应字段：

```json
{
  "exit_code": 0,
  "stdout": "CLI 原始输出",
  "stderr": "CLI 原始错误输出",
  "timed_out": false
}
```

超限时增加 `truncated`、`original_bytes` 和 `hint`。发现稳定新版本时增加：

```json
{"update_available":{"current_version":"1.0.81","latest_version":"1.0.82","upgrade_command":"sudo scripts/update-cli.sh 1.0.82"}}
```

该字段供服务器管理员处理。普通用户或 Agent只报告更新，不执行升级。

## 2. 身份、权限和写操作

`--as user` 使用服务器共享的飞书用户授权，适合个人日历、云空间等用户资源；`--as bot` 使用应用身份。bot 权限不足时去开发者后台开 scope，不能运行 `auth login`。user 权限不足时由管理员补 scope 或重新授权。

高风险写操作不带 `--yes` 时会返回退出码 10：

```json
{
  "ok": false,
  "error": {
    "type": "confirmation_required",
    "hint": "add --yes to confirm",
    "risk": {"level": "high-risk-write", "action": "drive +delete"}
  }
}
```

此时展示 action 和关键参数，等待用户明确确认。确认后只在原 `args` 末尾追加 `--yes`；拒绝则停止。不得把退出码 10 当网络错误自动重试。

## 3. 浏览 Skill

列出全部 Skill：

```json
{"action":"list"}
```

列出某个 Skill 的下一层：

```json
{"action":"list","path":"lark-doc/references"}
```

读取主文件或 reference：

```json
{"action":"read","path":"lark-doc"}
{"action":"read","path":"lark-doc/references/lark-doc-fetch.md"}
```

返回内容来自当前容器内 `lark-cli`，与 CLI 版本同步。先列目录再读取需要的文件，避免一次加载无关内容。

## 4. 远程限制

不可用能力包括 `auth`、`config`、`profile`、`update`、`doctor`，服务器本地文件、输出路径、剪贴板、本地 Apps 初始化和 Git 凭据，以及事件总线 daemon 管理。`event consume` 必须包含不超过 150 秒的 `--timeout`：

```json
{"args":["event","consume","im.message.receive_v1","--as","bot","--timeout","30s","--max-events","1"]}
```

本地文件上传、下载和剪贴板命令不会映射到调用方机器；优先使用 URL、飞书资源 token、内联 JSON 或 stdin。

## 5. 错误处理

- `exit_code=0`：命令成功；按 CLI JSON 或文本读取 stdout。
- `exit_code=10`：等待高风险确认。
- 其他非零码：先读 stderr；参数错误修正一次，权限错误按 user/bot 身份处理，429 退避一次，5xx 或网络错误退避重试一次。
- `timed_out=true`：缩小查询或拆分操作后重试一次。
- `truncated=true`：减少 page size、limit 或返回格式后重试。

服务不会把 CLI 业务错误改成 MCP 协议错误，因此调用方必须检查 `exit_code`。
