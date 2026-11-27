# Google Colab CLI MCP 配置

## 1. 访问与身份

客户端走 `https://colab.example.com/mcp`，登录 GitHub 后以 `COLAB_MCP_GITHUB_USERS` 校验身份。所有通过白名单的用户使用同一个 Google 账号、Colab 会话、历史和计算额度。只把需要共同操作这个账号的用户加入白名单。不同 Google 账号或独立信任边界各自部署实例并配置独立域名、端口、凭据与卷。

Compose 只监听宿主机回环端口 8770，外部流量由 Nginx 终止 TLS。TLS 模板覆盖 OAuth discovery、客户端注册、回调和 MCP 请求。客户端回调使用六个服务共用的[客户端回调白名单](../DEPLOYMENT.md#61-客户端回调白名单)。Nginx 与 MCP HTTP 访问日志关闭，以免授权回调参数进入访问日志。

## 2. 环境变量

| 变量 | 用途 |
|---|---|
| `COLAB_MCP_BASE_URL` | HTTPS origin，例如 `https://colab.example.com` |
| `COLAB_MCP_GITHUB_CLIENT_ID`、`COLAB_MCP_GITHUB_CLIENT_SECRET` | 独立 GitHub OAuth App，回调设置为该 origin 的 `/auth/callback` |
| `COLAB_MCP_GITHUB_USERS` | 逗号分隔的授权 GitHub login |
| `COLAB_MCP_JWT_SIGNING_KEY` | 独立 32 字节随机签名密钥的 hex 表示 |
| `COLAB_MCP_STORAGE_KEY` | 32 字节随机值的 URL-safe base64，用于 Redis 认证存储加密 |
| `COLAB_MCP_REDIS_PASSWORD` | MCP 和 Redis 环境文件中的密码一致 |
| `COLAB_MCP_ENV_FILE`、`COLAB_MCP_REDIS_ENV_FILE` | Compose 参数，分别覆盖两份宿主机环境文件路径 |
| `COLAB_MCP_ADC_PATH` | Compose 参数，覆盖宿主机 ADC 路径，默认为 `/etc/colab-cli-mcp/adc.json` |

前六行填入 MCP 环境文件。Redis 环境文件只需 Redis 密码。Compose 把 ADC 单文件只读挂载到 `/run/colab/adc.json`，容器 UID 10001 必须有读取权限。

## 3. Google 授权

在目标服务器按 Google 官方说明准备 `gcloud`。以有权限的部署账户为专用 Google 用户交互授权。下面使用临时 Cloud SDK 配置目录，避免覆盖该账户已有 ADC。通过受信任浏览器完成 Google 给出的授权流程。

```bash
umask 077
colab_auth_dir=$(mktemp -d)
CLOUDSDK_CONFIG="$colab_auth_dir" gcloud auth application-default login --no-launch-browser \
  --scopes=openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/colaboratory
sudo install -d -o root -g root -m 700 /etc/colab-cli-mcp
sudo test -e /etc/colab-cli-mcp/adc.json || \
  sudo install -o 10001 -g 10001 -m 400 \
    "$colab_auth_dir/application_default_credentials.json" /etc/colab-cli-mcp/adc.json
```

逐条执行，授权成功且确认 ADC 已安全落盘后，清理本次生成的临时配置目录。已有部署保留 ADC 路径。主动轮换时备份旧文件，再安装新文件并重建 `mcp` 容器，让 bind mount 读取新文件。使用 Docker user namespace 时，按目标实例的 UID 映射调整宿主机文件所有者。

参考 [Colab CLI 认证说明](https://github.com/googlecolab/google-colab-cli/blob/main/docs/04_automation_and_utility.md#required-scopes) 与 [gcloud 无头授权参数](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/login)。

启动后在目标服务器确认挂载与账号可用性。

```bash
sudo docker compose exec -T mcp test -r /run/colab/adc.json
sudo docker compose exec -T mcp env HOME=/var/lib/colab-mcp \
  GOOGLE_APPLICATION_CREDENTIALS=/run/colab/adc.json \
  /app/.venv/bin/python -B colab_entry.py --auth adc usage
```

若 Google 返回 401 或 403，检查账号 Colab 访问权限、授权范围和 ADC 是否已撤销。检查时仅记录状态与错误类型。MCP 客户端通过 GitHub 登录，Google 凭据由服务器保存。

## 4. 工具与验收

`colab_cli(args, stdin?, file_base64?)` 使用参数数组，首项为子命令。客户端通过 `tools/list` 获取工具说明、参数描述和示例。完整命令、选项、调用与返回字段见 [AGENTS.md](AGENTS.md)。传入的 Python 在 Colab 内核中执行。MCP 对临时工作目录中的输入输出文件进行封装，所有上传和输出文件按字节计算上限 4 MiB。

一次只执行一个 CLI 调用，以避免共享会话切换、终止和执行重叠。每次调用最多 240 秒，包括 CLI 启动时间。超时会结束本地 CLI 进程组，远端运行时和任务可能继续执行。启动、执行和停止失败后先检查 `sessions`、`status`，再决定是否重试。

如果分配请求超时，运行时可能已创建而本地会话记录尚未写入。对无法通过 CLI 名称定位的运行时，在 Google Colab 网页的会话管理页确认并释放。

按统一手册验收 TLS、服务发现、未授权 401、GitHub 白名单以及 `tools/list`。用获准客户端依次执行下列工具调用。

1. `{"args":["usage"]}`
2. `{"args":["new","-s","mcp-smoke"]}`
3. `{"args":["exec","-s","mcp-smoke"],"stdin":"print(2 + 2)"}`
4. `{"args":["stop","-s","mcp-smoke"]}`

首次测试选择一个未占用的会话名。用 `status` 确认输出中会话已释放。执行中断时仍应检查并清理已创建的会话。测试涉及 Google Colab 计算额度。
