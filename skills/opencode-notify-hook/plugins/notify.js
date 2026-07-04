// opencode notification plugin
// Events: permission.asked (needs auth) + session.idle (task done)
// 投递链: terminal-notifier → osascript → bell (本地) ; 飞书文本消息 (可选)
//
// 注意: 全部 shell 调用走 node:child_process (stdio ignore/pipe)，不使用 opencode 的 $
// helper —— 否则 opencode 会把 plugin 内的 $ 命令回显到 TUI（如 lsappinfo 的 ASN 输出
// 变成"顶层消息"遮盖终端）。child_process 对 opencode TUI 完全不可见。

const TITLE = "OpenCode"
const SOUND = "Glass"
const APPROVAL_PYTHON = process.env.FEISHU_APPROVAL_PYTHON ?? "/Users/charles/Nutstore/agent-space/.venv/bin/python"
const APPROVAL_SEND = process.env.FEISHU_APPROVAL_SEND ?? "/Users/charles/.codex/hooks/feishu_send_approval.py"

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

export const OpenCodeNotifyPlugin = async () => {
  const projectName = () => process.env.NOTIFY_PROJECT_DIR?.split("/").filter(Boolean).at(-1) ?? process.cwd().split("/").filter(Boolean).at(-1) ?? "unknown"

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

  // 本地通知：terminal-notifier → osascript → bell（都不向 TUI 输出任何东西）
  async function notify(title, message) {
    const project = projectName()
    const tn = await run("terminal-notifier", ["-title", title, "-subtitle", project, "-message", message, "-sound", SOUND])
    if (tn.code === 0) {
      void larkNotify(title, project, message).catch(() => {})
      return
    }
    const script = `display notification ${JSON.stringify(message)} with title ${JSON.stringify(title)} subtitle ${JSON.stringify(project)} sound name ${JSON.stringify(SOUND)}`
    const osa = await run("osascript", ["-e", script])
    if (osa.code === 0) {
      void larkNotify(title, project, message).catch(() => {})
      return
    }
    process.stdout.write("\a") // terminal bell last resort
    void larkNotify(title, project, message).catch(() => {})
  }

  async function approvalNotify(message) {
    const project = projectName()
    await notify(TITLE, message)
    const approvalId = `opencode-${Math.floor(Date.now() / 1000)}-${process.pid}`
    await sendFeishuApproval(approvalId, project, message)
  }

  async function sendFeishuApproval(approvalId, project, content) {
    const fs = await import("node:fs/promises")
    try {
      await fs.access(APPROVAL_SEND)
      const { spawn } = await import("node:child_process")
      const child = spawn(APPROVAL_PYTHON, [
        APPROVAL_SEND,
        "--agent", TITLE,
        "--approval-id", approvalId,
        "--project", project,
        "--content", content,
      ], { stdio: "ignore" })
      const code = await new Promise(resolve => child.on("close", resolve))
      return code === 0
    } catch {
      return false
    }
  }

  async function larkNotify(agent, project, content) {
    const url = process.env.LARK_WEBHOOK_URL
    if (!url) return
    const payload = {
      msg_type: "text",
      content: { text: `Agent: ${agent}\nProject: ${project}\nContent: ${content}` },
    }
    const secret = process.env.LARK_WEBHOOK_SECRET?.trim()
    if (secret) {
      const crypto = await import("node:crypto")
      const timestamp = String(Math.floor(Date.now() / 1000))
      payload.timestamp = timestamp
      payload.sign = crypto.createHmac("sha256", `${timestamp}\n${secret}`).digest("base64")
    }
    const body = JSON.stringify(payload)
    const { request } = await (url.startsWith("https:")
      ? import("node:https")
      : import("node:http"))
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
        await approvalNotify(`需要操作: ${extractPermissionMsg(event)}`)
      }
      if (event.type === "session.idle") {
        if (await isFocused()) return
        await notify(TITLE, "任务已完成")
      }
    },
  }
}
