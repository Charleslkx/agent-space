# OpenCode 焦点检测（ASN 问题）

焦点检测决定「用户正盯着会话窗口时是否静默」。实现上有个 macOS `lsappinfo` 的坑必须避开。

## 问题

`lsappinfo front` **不返回 bundle ID**，只返回一个 ASN 标识符：

```
$ lsappinfo front
ASN:0x0-0x1829828:
```

旧版代码用 `/__CFBundleIdentifier="([^"]+)"/` 正则直接匹配 `lsappinfo front` 的输出，**永远匹配不上** —— `bundleId` 恒为空，`isFocused()` 恒返回 `false`，焦点检测完全失效（无论用户是否在看终端，都会弹通知）。

## 修复：两步 lsappinfo

正确做法是分两步：先拿 ASN，再用 ASN 查 bundle ID。

```js
// 1. 拿前台 ASN
const { stdout: asn } = await run("lsappinfo", ["front"])   // "ASN:0x0-0x1829828:"

// 2. 用 ASN 查 bundle ID
const { stdout: info } = await run("lsappinfo", ["info", "-only", "bundleid", asn.trim()])
// 输出: "CFBundleIdentifier"="com.exafunction.windsurf"
const front = info.match(/"CFBundleIdentifier"="([^"]+)"/)?.[1] ?? ""
```

## owner vs front 比较

光知道前台 app 的 bundle ID 还不够 —— 旧代码只判断「前台是不是任意终端」
（`TERMINAL_BUNDLES.some(b => bundleId.startsWith(b))`），会导致 opencode 跑在
Terminal 里、用户切到 iTerm2 时也被误判为「聚焦」而静默。

修复后引入 `owner = process.env.__CFBundleIdentifier`（macOS 注入的、承载 opencode
的那个终端的 bundle ID），只有 `front === owner` 时才静默：

```js
async function isFocused() {
  const owner = process.env.__CFBundleIdentifier
  if (!owner) return false          // SSH/远程拿不到 owner → 永远通知
  const front = await frontBundleId()
  if (!front) return false
  return TERMINAL_BUNDLES.includes(owner) && front === owner
}
```

## 为什么不用 opencode 的 `$` helper

插件内所有 shell 调用都走 `node:child_process`（`stdio: ignore/pipe`），而不是 opencode
的 `$` 模板 helper。原因：

1. `$` 会把命令输出回显到 opencode TUI —— `lsappinfo` 的 ASN 输出会变成「顶层消息」
   遮盖终端界面。
2. 两步 `lsappinfo` 需要把第一步的 ASN 传给第二步，用 `child_process` 的 `run()` 封装
   更直观。

`child_process` 对 opencode TUI 完全不可见，不会污染界面。

## 调试

```bash
# 确认 lsappinfo 输出格式
lsappinfo front                      # 应输出 ASN:0x...
lsappinfo info -only bundleid $(lsappinfo front)   # 应输出 "CFBundleIdentifier"="..."

# 确认 owner
echo $__CFBundleIdentifier            # 承载 opencode 的终端 bundle ID

# 在 node 里直接测插件
node --input-type=module <<'EOF'
import { OpenCodeNotifyPlugin } from "./plugins/notify.js"
const plugin = await OpenCodeNotifyPlugin()
console.log("plugin hooks:", Object.keys(plugin))
EOF
```
