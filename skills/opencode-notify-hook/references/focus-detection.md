# OpenCode 焦点检测

## 1. macOS 查询

`lsappinfo front` 返回 ASN，需要第二次查询取得 bundle ID：

```js
const { stdout: asn } = await run("lsappinfo", ["front"])
const { stdout: info } = await run("lsappinfo", ["info", "-only", "bundleid", asn.trim()])
const front = info.match(/"CFBundleIdentifier"="([^"]+)"/)?.[1] ?? ""
```

将 `front` 与承载当前 OpenCode 进程的 `process.env.__CFBundleIdentifier` 比较。当前实现还要求 owner 在 `TERMINAL_BUNDLES` 中；只判断前台是否为任意终端会误抑制其他终端里的会话。缺少 owner 或前台信息时继续通知。

这是应用级识别，无法区分同一应用的多个窗口。平台逻辑以 `plugins/notify.js` 的 `isFocused()` 为准。

## 2. 调试与实现边界

```bash
lsappinfo front
lsappinfo info -only bundleid "$(lsappinfo front)"
printf '%s\n' "${__CFBundleIdentifier:-}"
```

插件使用 `node:child_process` 捕获命令输出，避免 OpenCode 的 shell helper 将 ASN 回显到 TUI。调试不应发送实际通知；插件结构检查见主 Skill 的验证步骤。
