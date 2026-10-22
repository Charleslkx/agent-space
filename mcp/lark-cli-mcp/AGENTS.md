# Lark CLI MCP — Agent guide

## Connect

Use `https://lark.{base-domain}/mcp` and complete GitHub OAuth. An allowed GitHub login grants access to one shared server-side Lark identity.

## Tools

`lark_cli(args, stdin?)` runs allowed CLI commands. `args` is the array after `lark-cli`; never pass a shell string or include the binary name. **The command must be `args[0]` and its subcommand `args[1]`** — flags may not precede either. `["--as","user","calendar","+agenda"]` is rejected; write `["calendar","+agenda","--as","user"]`.

`lark_cli_skill(action, path?)` exposes the version-matched embedded Skill system. Start with `{"action":"list"}`, then list one directory layer and read only the required `SKILL.md` or reference files.

`begin_lark_auth(domains?)` starts a device authorization for the shared Lark user (default `["all"]`) and returns `verification_url`, `device_code`, and `qr_code_png_base64` (PNG, may be null). `complete_lark_auth(device_code)` finishes it and returns the raw CLI result.

## Required behavior

1. Choose `--as user` for shared user resources and `--as bot` for app identity. For bot scope errors, use the returned developer-console URL. For user login or user scope errors, call `begin_lark_auth`, show the user the URL and QR code, end the turn, and call `complete_lark_auth` only after the user says they approved. Completing it replaces the login every caller shares, so never start it unprompted for a working login.
2. Always inspect `exit_code`, stdout, stderr, `timed_out`, and `truncated`.
3. Exit code 10 with `confirmation_required` is a mandatory stop. Show the action and target to the user. Only after explicit confirmation append `--yes` to the original argv and retry once. Never add it preemptively.
4. If `update_available` exists, mention the current and latest versions. Do not run the upgrade command; it is a server-administrator action.
5. Use URLs, resource tokens, inline JSON, or stdin. Server-local files, output paths, clipboard access, profiles, `auth` through `lark_cli`, configuration, updates, and unbounded event consumers are blocked. `@file` is rejected in every spelling, including `--flag=@path`; only `@-` (stdin) works.
6. A `server is at capacity` error means nothing ran — retry in a few seconds.

## Examples

```json
{"args":["calendar","+agenda","--as","user"]}
{"args":["schema","drive.file.list","--format","json"]}
{"action":"read","path":"lark-calendar"}
```

For event consumption, always provide `--timeout` of 150 seconds or less; use `--max-events` when the desired count is known. Every `--timeout` you pass must satisfy the ceiling — repeating the flag does not raise it — and `consume` must be `args[1]`.
