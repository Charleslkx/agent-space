// opencode notification plugin
// Events: permission.asked (needs auth) + session.idle (task done)
// 投递链: macOS terminal-notifier/osascript；WSL Windows Toast；原生 Linux 仅飞书
//
// 飞书通道自带 IM API 发送（无 codex 依赖），读取 FEISHU_ENV 指向的 env 文件
// （默认 ~/.config/opencode/feishu-agent.env）里的 FEISHU_APP_ID/SECRET/HOME_CHANNEL，
// 取 tenant token 后投递纯通知卡片（无按钮）。kind=done 绿色；kind=approval 橙色。
// IM API 失败时回退 LARK_WEBHOOK_URL webhook（同款卡片）。
//
// 注意: 全部 shell 调用走 node:child_process (stdio ignore/pipe)，不使用 opencode 的 $
// helper —— 否则 opencode 会把 plugin 内的 $ 命令回显到 TUI（如 lsappinfo 的 ASN 输出
// 变成"顶层消息"遮盖终端）。child_process 对 opencode TUI 完全不可见。

const TITLE = "OpenCode"
const SOUND = "Glass"
const FEISHU_ENV = process.env.FEISHU_ENV ?? `${process.env.HOME}/.config/opencode/feishu-agent.env`

// Terminal app bundle IDs that host opencode (add yours if missing)
const TERMINAL_BUNDLES = [
  "com.apple.Terminal",
  "com.googlecode.iterm2",
  "dev.warp.Warp-Stable",
  "net.kovidgoyal.kitty",
  "co.zeit.hyper",
  "com.mitchellh.ghostty",
  "ai.opencode.app",
]

async function run(cmd, args) {
  const { spawn } = await import("node:child_process")
  return new Promise(resolve => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] })
    let out = ""
    child.stdout?.on("data", d => { out += d })
    child.on("error", () => resolve({ code: 1, stdout: out }))
    child.on("close", code => resolve({ code: code ?? 1, stdout: out }))
  })
}

// POST JSON，返回解析后的响应（供飞书 IM API 用）
async function httpsPostJson(url, payload, headers) {
  const https = await import("node:https")
  const body = JSON.stringify(payload)
  return new Promise((resolve, reject) => {
    const req = https.request(url, {
      method: "POST",
      timeout: 10000,
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body), ...(headers ?? {}) },
    }, res => {
      let data = ""
      res.on("data", d => { data += d })
      res.on("end", () => { try { resolve(JSON.parse(data)) } catch (e) { reject(e) } })
    })
    req.on("error", reject)
    req.on("timeout", () => req.destroy(new Error("timeout")))
    req.end(body)
  })
}

function parseEnv(text) {
  const env = {}
  for (const line of text.split("\n")) {
    const t = line.trim()
    if (!t || t.startsWith("#") || !t.includes("=")) continue
    const i = t.indexOf("=")
    env[t.slice(0, i)] = t.slice(i + 1)
  }
  return env
}

// 绿=完成/橙=授权 的纯通知卡片（无按钮），IM API 与 webhook 共用
function buildCard(agent, project, content, kind) {
  const [title, template] = kind === "approval"
    ? [`⚠️ ${agent} · 需要授权`, "orange"]
    : [`🤖 ${agent} · 任务完成`, "green"]
  const ts = new Date().toLocaleString("sv-SE") // YYYY-MM-DD HH:MM:SS
  return {
    config: { wide_screen_mode: true },
    header: { title: { tag: "plain_text", content: title }, template },
    elements: [
      { tag: "div", fields: [
        { is_short: true, text: { tag: "lark_md", content: `**Agent**\n${agent}` } },
        { is_short: true, text: { tag: "lark_md", content: `**Project**\n${project}` } },
      ]},
      { tag: "hr" },
      { tag: "div", text: { tag: "lark_md", content } },
      { tag: "note", elements: [{ tag: "plain_text", content: `🕒 ${ts}` }] },
    ],
  }
}

export const OpenCodeNotifyPlugin = async () => {
  const projectName = () => process.env.NOTIFY_PROJECT_DIR?.split("/").filter(Boolean).at(-1) ?? process.cwd().split("/").filter(Boolean).at(-1) ?? "unknown"

  async function localNotificationsEnabled() {
    if (process.platform === "darwin") return true
    if (process.platform !== "linux") return false
    if (process.env.WSL_INTEROP) return true
    try {
      return /microsoft|wsl/i.test(await (await import("node:fs/promises")).readFile("/proc/version", "utf8"))
    } catch {
      return false
    }
  }

  // 焦点检查：opencode 运行在终端里，若该终端正是最前 app 则静默。
  //
  // ASN 问题（已修复）：
  //   `lsappinfo front` 输出的是 ASN 标识符（如 `ASN:0x0-0x1829828:`），不是 bundle ID。
  //   旧代码用 /__CFBundleIdentifier="([^"]+)"/ 正则匹配 `lsappinfo front` 输出，永远匹配
  //   不上 → bundleId 恒空 → isFocused() 恒 false → 焦点检测完全失效（永远通知）。
  //
  //   正确做法是两步：
  //     1. `lsappinfo front` → 拿到前台 app 的 ASN（如 `ASN:0x0-0x1829828:`）
  //     2. `lsappinfo info -only bundleid <ASN>` → 拿到 `"CFBundleIdentifier"="com.x"`
  //
  //   另外，旧代码只判断"前台是不是任意终端"（TERMINAL_BUNDLES.some(startsWith)），
  //   会导致 opencode 跑在 Terminal 里、用户切到 iTerm2 时也被误判为"聚焦"而静默。
  //   修复后比较 front === owner（owner = __CFBundleIdentifier，即承载 opencode 的终端），
  //   只有前台正好是同一个终端时才静默。
  async function isFocused() {
    try {
      if (process.platform !== "darwin") return false
      const owner = process.env.__CFBundleIdentifier
      if (!owner) return false // SSH/远程或拿不到 owner → 永远通知
      const front = await frontBundleId()
      if (!front) return false
      return TERMINAL_BUNDLES.includes(owner) && front === owner
    } catch {
      return false
    }
  }

  // 两步 lsappinfo 拿前台 app 的 bundle ID（见上方 ASN 问题注释）
  async function frontBundleId() {
    const { stdout: asn } = await run("lsappinfo", ["front"])
    const id = asn.trim()
    if (!id) return ""
    const { stdout: info } = await run("lsappinfo", ["info", "-only", "bundleid", id])
    return info.match(/"CFBundleIdentifier"="([^"]+)"/)?.[1] ?? ""
  }

  // 本地通知：macOS terminal-notifier → osascript → bell；WSL 使用 Windows Toast。
  async function notify(title, project, message) {
    if (!await localNotificationsEnabled()) return
    if (process.platform === "linux") {
      const quote = value => String(value).replaceAll("'", "''")
      const script = `[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null; [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime] > $null; $x=New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$([System.Security.SecurityElement]::Escape('${quote(title)}'))</text><text>$([System.Security.SecurityElement]::Escape('${quote(project)}'))</text><text>$([System.Security.SecurityElement]::Escape('${quote(message)}'))</text></binding></visual></toast>\"); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('OpenCode').Show((New-Object Windows.UI.Notifications.ToastNotification $x))`
      await run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script])
      return
    }
    const tn = await run("terminal-notifier", ["-title", title, "-subtitle", project, "-message", message, "-sound", SOUND])
    if (tn.code === 0) return
    const script = `display notification ${JSON.stringify(message)} with title ${JSON.stringify(title)} subtitle ${JSON.stringify(project)} sound name ${JSON.stringify(SOUND)}`
    const osa = await run("osascript", ["-e", script])
    if (osa.code === 0) return
    process.stdout.write("\a") // terminal bell last resort
  }

  // 飞书主通道：自带 IM API 发卡片，失败回退 webhook。全程 stdlib，无 codex 依赖。
  async function feishuNotify(kind, project, content) {
    if (await feishuCard(kind, project, content)) return
    await larkNotify(kind, project, content).catch(() => {})
  }

  async function feishuCard(kind, project, content) {
    try {
      const fs = await import("node:fs/promises")
      const env = parseEnv(await fs.readFile(FEISHU_ENV, "utf8"))
      const appId = env.FEISHU_APP_ID
      const appSecret = env.FEISHU_APP_SECRET
      const receiveId = env.FEISHU_HOME_CHANNEL || env.FEISHU_APPROVAL_RECEIVE_ID
      const receiveIdType = env.FEISHU_APPROVAL_RECEIVE_ID_TYPE || "chat_id"
      if (!appId || !appSecret || !receiveId) return false
      const tok = await httpsPostJson(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        { app_id: appId, app_secret: appSecret },
      )
      if (tok.code !== 0) return false
      const resp = await httpsPostJson(
        `https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=${receiveIdType}`,
        { receive_id: receiveId, msg_type: "interactive", content: JSON.stringify(buildCard(TITLE, project, content, kind)) },
        { Authorization: `Bearer ${tok.tenant_access_token}` },
      )
      return resp.code === 0
    } catch {
      return false
    }
  }

  // webhook 兜底：仅在 IM API 失败且配置了 LARK_WEBHOOK_URL 时使用，发同款卡片
  async function larkNotify(kind, project, content) {
    const url = process.env.LARK_WEBHOOK_URL
    if (!url) return
    const payload = { msg_type: "interactive", card: buildCard(TITLE, project, content, kind) }
    const secret = process.env.LARK_WEBHOOK_SECRET?.trim()
    if (secret) {
      const crypto = await import("node:crypto")
      const timestamp = String(Math.floor(Date.now() / 1000))
      payload.timestamp = timestamp
      payload.sign = crypto.createHmac("sha256", `${timestamp}\n${secret}`).digest("base64")
    }
    const body = JSON.stringify(payload)
    const { request } = await (url.startsWith("https:") ? import("node:https") : import("node:http"))
    await new Promise(resolve => {
      const req = request(url, { method: "POST", headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }, timeout: 5000 }, res => {
        res.resume()
        res.on("end", resolve)
      })
      req.on("error", resolve)
      req.on("timeout", () => req.destroy())
      req.end(body)
    })
  }

  function extractPermissionMsg(event) {
    const p = event.properties ?? {}
    // Try common field shapes across opencode versions
    const tool = p.tool ?? p.toolName ?? p.name ?? ""
    const args = p.args ?? p.input ?? {}
    const desc = args.description ?? args.desc ?? ""
    const cmd = args.command ?? args.cmd ?? ""
    if (desc) return tool ? `${tool} — ${desc}` : desc
    if (cmd) return tool ? `${tool} — ${String(cmd).slice(0, 80)}` : String(cmd).slice(0, 80)
    return tool ? `需要授权: ${tool}` : "需要授权"
  }

  return {
    event: async ({ event }) => {
      if (event.type === "permission.asked") {
        if (await isFocused()) return
        const project = projectName()
        const msg = `需要操作: ${extractPermissionMsg(event)}`
        await notify(TITLE, project, msg)
        await feishuNotify("approval", project, msg)
      }
      if (event.type === "session.idle") {
        if (await isFocused()) return
        const project = projectName()
        await notify(TITLE, project, "任务已完成")
        await feishuNotify("done", project, "任务已完成")
      }
    },
  }
}
