# Lark CLI 远程 MCP 实施计划

## 1. 目标与交付标准

在 `mcp/lark-cli-mcp` 新建独立、可分发的远程 MCP，将 `lark-cli` 的飞书业务能力通过 Streamable HTTP 提供给 MCP 客户端，并增加一个按需浏览 `lark-cli` 内嵌 Skill 体系的工具。现有 `mcp/lark-markdown` 保持独立，不合并、不替换。

分发包必须让没有本仓库背景的人类用户或 Agent 仅依靠随包文档完成环境检查、GitHub OAuth、飞书应用与共享身份配置、部署、客户端接入、日常调用、升级和故障排查。所有示例公网地址统一采用 `https://lark.{your-domain}`；用户只填写基础域名，`lark` 前缀不可修改。

## 2. 服务接口与执行边界

服务公开两个工具：

```text
lark_cli(args: string[], stdin?: string)
lark_cli_skill(action: "list" | "read", path?: string)
```

### 2.1 `lark_cli`

`args` 是 `lark-cli` 二进制之后的参数数组，不接受 shell 字符串。子进程必须通过 argv 启动，原样返回 `exit_code`、`stdout`、`stderr` 和 `timed_out`；输出超限时附加 `truncated`、`original_bytes` 和缩小请求的提示。

固定边界为：最多 128 个参数、每参数 16 KiB、stdin 1 MiB、stdout 和 stderr 各 10 MiB、普通调用超时 180 秒。CLI 返回退出码 10 和 `confirmation_required` 时直接透传；服务不得自动追加 `--yes`、重试、回滚或绕过审批。工具按保守方式声明 `readOnlyHint=false`、`destructiveHint=true`、`idempotentHint=false`、`openWorldHint=true`。

服务自行识别 `lark-cli` 更新，不依赖 CLI 每次调用时执行联网检查。子进程设置 `LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1`，避免更新通知污染原始 stdout/stderr；MCP 在后台查询官方 GitHub 最新稳定 Release，并把已缓存的更新信息附加到工具响应顶层：

```json
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "timed_out": false,
  "update_available": {
    "current_version": "1.0.81",
    "latest_version": "1.0.82",
    "upgrade_command": "sudo scripts/update-cli.sh 1.0.82"
  }
}
```

没有更新或尚未取得检查结果时省略 `update_available`。首次工具调用只触发后台线程，不等待 GitHub；结果缓存 6 小时，任一时刻最多一个检查任务。网络错误、GitHub 限流、无效响应或版本解析失败只记录 debug 日志，不改变 CLI 调用结果。只比较正式语义版本并忽略 prerelease；`LARK_CLI_MCP_UPDATE_CHECK=0` 可完全关闭检查。服务只报告更新，不下载、不重启、不自动执行升级脚本。

开放全部飞书业务域以及 `api`、`schema`、`help`、`skills`。固定拒绝会修改服务器状态或依赖客户端本机环境的入口：

- 禁止 `auth`、`config`、`profile`、`update`、`doctor` 和全局 `--profile`；飞书认证由部署管理员在服务器上完成。
- Apps 保留云端业务命令，禁止 `+init`、`+env-pull`、`+git-credential-*` 等本地工程与凭据操作。
- Event 保留 `list`、`schema`；禁止 `status`、`stop`。`event consume` 必须显式包含不超过 150 秒的 `--timeout`，禁止无界监听和 `--output-dir`。
- 禁止 `--from-clipboard`、`--file`、`--output`、`--output-dir`、`--local-dir` 及 `@本地文件` 等服务器文件入口。
- 首版不提供文件上传桥、下载文件回传或剪贴板模拟；URL、资源 token、内联 JSON 和 stdin 仍可使用。

### 2.2 `lark_cli_skill`

该工具直接调用随当前 `lark-cli` 二进制内嵌的 Skill 内容，不在 MCP 项目中复制另一份 Skill：

- `action="list"` 且无 `path`：执行 `lark-cli skills list --json`，返回所有 Skill 的名称、描述、版本和元数据。
- `action="list"` 且有 `path`：执行 `lark-cli skills list <path> --json`，列出指定 Skill 或目录的下一层。
- `action="read"`：要求 `path`，执行 `lark-cli skills read <path> --json`，返回 `SKILL.md` 或 reference 文件。
- 拒绝绝对路径、`..`、空路径段和路径逃逸；不提供一次性全量正文返回。
- 工具声明 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`。

## 3. 认证、状态与部署架构

### 3.1 GitHub OAuth

沿用 Brave 和 Firecrawl MCP 已验证的 FastMCP GitHub OAuth 结构：Streamable HTTP、`read:user`、大小写不敏感的 GitHub login 白名单、稳定 JWT 签名密钥、Fernet 加密的 Redis OAuth 状态、protected-resource discovery、audience 校验，以及 ChatGPT、Claude、Codex、OpenCode、WorkBuddy 和 localhost 回调兼容。

GitHub OAuth App、Redis、JWT、Fernet 密钥和状态卷必须独立，不能与 Brave、Firecrawl 或 Lark-Markdown 共用。未认证 `/mcp` 返回 401 和正确的 protected-resource metadata；非白名单用户即使完成 GitHub 登录也不能列出工具。

### 3.2 飞书身份

首版使用单一共享飞书身份。所有 GitHub 白名单用户共享服务器上的飞书应用配置、用户授权和 bot 身份，可在业务命令中选择 `--as user` 或 `--as bot`。白名单只能包含互相信任的用户。

飞书配置、refresh token 和 profile 保存到独立 `lark-state` Docker volume。管理员通过挂载同一 volume 的 Compose 命令依次执行 `lark-cli config init --new`、`lark-cli auth login --domain all` 和 `lark-cli auth status --json --verify`。MCP 调用方不能远程发起、替换或注销这份共享认证。

### 3.3 容器和网络

Docker 镜像固定安装实施时确认的最新稳定 `lark-cli`；初始基线为 `1.0.81`。镜像从官方 GitHub Release 下载 Linux amd64 或 arm64 资产，并用同一 Release 的 `checksums.txt` 校验。运行时只检测并报告新版本，实际升级必须由管理员显式运行 `sudo scripts/update-cli.sh [version]`，完成校验、重建、健康检查和版本冒烟后才切换容器。

Compose 包含 `state-init`、`mcp`、`redis-init`、`redis` 四个服务；两个 init 容器只负责把持久卷交给各自的非 root 运行用户，完成后退出：

- MCP 与 Redis 均以非 root 身份运行，根文件系统只读，启用 `no-new-privileges` 并删除不需要的 capabilities。
- MCP 仅将 `/tmp` 和 `lark-state` 设为可写；Redis 使用独立持久卷且不映射宿主端口。
- MCP 绑定宿主 `127.0.0.1:8768`，公网只开放 Nginx 的 80/443。
- Nginx 必须代理 `/mcp`、`/.well-known/*`、OAuth 注册、授权、token 和 callback 等全部 FastMCP 路径。

## 4. 可分发目录与文档

交付目录至少包含：

```text
lark-cli-mcp/
├── server.py
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── compose.yaml
├── .env.example
├── .redis.env.example
├── README.md
├── CONFIGURATION.md
├── USAGE.md
├── AGENTS.md
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── deploy/
│   ├── lark-mcp.bootstrap.nginx.conf
│   └── lark-mcp.nginx.conf
├── scripts/
│   ├── ubuntu.sh
│   └── update-cli.sh
└── tests/
    └── test_server.py
```

分发包不得包含 OAuth Secret、飞书应用凭据、Redis 密码、Token、授权状态、证书、实际 `.env` 或本地缓存。

### 4.1 文档职责

`README.md` 是唯一入口，说明用途、架构、两个工具、支持边界、快速部署，并链接其他文档。

`CONFIGURATION.md` 面向部署人员，完整覆盖环境要求、GitHub OAuth App、白名单、Redis/JWT/Fernet、飞书应用与共享授权、Compose、Nginx、TLS、防火墙、客户端接入、更新检查开关、显式升级、备份、密钥轮换和分层故障处理。

`USAGE.md` 面向人类使用者，提供参数格式、stdin、输出字段、退出码、`update_available` 的含义和处理方式、查询和写入示例、user/bot 切换、高风险确认流程、文件与事件限制，以及 Skill 的逐层浏览方式。

`AGENTS.md` 面向 Agent，使用短而完整的操作契约说明 argv 传参、Skill 定位、身份和权限错误处理、退出码 10 审批、禁止自动 `--yes`、输出截断和 `update_available` 处理；Agent 只能向用户报告可升级，不得自行调用服务器升级脚本。

所有命令必须可直接复制，只保留域名、GitHub login 和凭据等明确占位符；每个占位符注明来源、格式、存放位置和轮换影响。

### 4.2 域名约束

部署脚本只接受基础域名：

```bash
BASE_DOMAIN=example.com scripts/ubuntu.sh check
sudo BASE_DOMAIN=example.com scripts/ubuntu.sh install
```

脚本内部生成 `lark.${BASE_DOMAIN}`，拒绝空值、协议、路径、端口和用户自定义的其他子域名前缀。所有配置必须由同一基础域名派生：

```text
Homepage URL                 https://lark.example.com
OAuth callback              https://lark.example.com/auth/callback
MCP endpoint                https://lark.example.com/mcp
Protected resource metadata https://lark.example.com/.well-known/oauth-protected-resource/mcp
```

环境变量统一使用独立前缀，例如：

```dotenv
LARK_CLI_MCP_BASE_URL=https://lark.example.com
LARK_CLI_MCP_GITHUB_CLIENT_ID=...
LARK_CLI_MCP_GITHUB_CLIENT_SECRET=...
LARK_CLI_MCP_GITHUB_USERS=alice,bob
LARK_CLI_MCP_UPDATE_CHECK=1
```

## 5. 测试与验收

单元测试覆盖 argv 透传、stdin、原始输出、超时、截断、shell 元字符、受限命令和参数、bounded event、退出码 10、Skill 列表/读取/路径逃逸、GitHub 白名单、OAuth 回调兼容、Redis 加密状态和缺失配置的失败行为。更新检查单独验证后台非阻塞、6 小时缓存、单飞行、稳定版比较、关闭开关、发现更新时附加字段，以及网络失败时仍完整返回原 CLI 结果。

构建与集成检查包括 Python 测试、`uv lock --check`、Compose 解析、Shell 语法、`git diff --check`，以及 Linux amd64、arm64 镜像中的校验和、`lark-cli --version`、`skills list` 和 `skills read`。

远程验收必须验证：

- 未认证请求得到 401 和正确 discovery；白名单用户可以完成 OAuth，非白名单用户不能发现工具。
- 共享飞书身份通过 `auth status --verify`，并成功执行一个 `schema`、一个真实只读业务调用和一个专用测试资源上的创建与回读。
- 高风险写操作只执行 `--dry-run` 并验证退出码 10，不执行真实删除。
- 用受控的假 Release 响应确认 `update_available` 字段；再模拟超时、429 和无效 JSON，确认业务调用的退出码、stdout、stderr 和延迟不受影响。
- Brave、Firecrawl 和 Lark-Markdown 在部署后仍可连接，端口、Nginx、OAuth App、Redis volume 和工具名互不冲突。

最终执行一次空白机器验收：测试人员只能获得该分发目录，必须能独立完成 `https://lark.{your-domain}/mcp` 部署、GitHub OAuth、飞书共享身份授权、客户端连接、Skill 浏览和业务调用。文档必须能把故障定位到 DNS/TLS、GitHub OAuth、MCP、Redis、飞书应用权限、飞书用户授权或 CLI 参数中的具体层级。
