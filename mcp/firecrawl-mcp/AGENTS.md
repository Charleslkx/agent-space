# Firecrawl CLI MCP — agent guide

Everything an agent needs to connect and call this MCP correctly, without reading the source or falling back to `firecrawl_skill` for basic usage. This file can be copied whole into another agent project's context.

## 1. Connect

```bash
claude mcp add --transport http firecrawl https://{your-domain}/mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.firecrawl]
url = "https://{your-domain}/mcp"
```

ChatGPT / Claude.ai / Claude Desktop: add the same URL via their remote-connector UI.

First call triggers a browser-based GitHub OAuth flow. Your GitHub login must be on the server's allowlist (`FIRECRAWL_MCP_GITHUB_USERS`) — if you're not, the tools simply won't appear in `tools/list`, not an explicit "denied" error.

Two tools are exposed: `firecrawl_cli(args, stdin?)` runs the CLI; `firecrawl_skill(name)` returns optional deeper reference docs. You should rarely need the second one — the first tool's own description already covers every allowed command's key flags and a ready example.

## 2. Which command

| Situation | Command |
|---|---|
| No URL yet — find pages, answer a question | `search` |
| Have a URL, want its content | `scrape` |
| Know the site, need one specific subpage | `map --search` (then `scrape` the result) |
| Need bulk content from a whole site section | `crawl` |
| Need AI-driven structured extraction from a complex multi-page site | `agent` |
| arXiv papers / GitHub history research | `research search-papers` / `research search-github` / etc |
| Check remaining credits | `credit-usage` |

Full flag tables live in the `firecrawl_cli` tool description itself (call `tools/list` or just read the tool's docstring) — this file only covers what's easy to get wrong.

## 3. Parameter rules

- `args` is the argument array **after** `firecrawl` — not a shell string, don't include `firecrawl` itself. `firecrawl_cli(["search", "my query", "--limit", "5"])`.
- **The subcommand must be `args[0]`** — flags may not precede it. `["--limit", "5", "search", "q"]` is rejected; write `["search", "q", "--limit", "5"]`.
- A `server is at capacity` error means nothing ran — retry in a few seconds.
- Pass URLs as their own array element, unquoted — the array boundary already does what shell-quoting would.
- Inline JSON, not files: `--schema`, `--actions`, `--scrape-options` take inline JSON strings (`--schema '{"type":"object",...}'`). Their `*-file` counterparts (`--schema-file` etc.) are blocked — there's no shared filesystem between you and this server.
- Results come back in the tool response's `stdout` field, not written anywhere. `-o/--output` is blocked for the same reason.
- **`crawl` and `agent` are async by default** — they return a job id immediately unless you pass `--wait`. This tool times out at 180s, so do NOT pass `--wait` on anything but a tiny job. Instead:
  1. `firecrawl_cli(["crawl", "https://example.com/docs", "--limit", "50"])` → get `<jobId>` from the response.
  2. Poll: `firecrawl_cli(["crawl", "<jobId>", "--status"])` until it's done.
  (Same pattern for `agent`.)

## 4. Boundaries and errors

Blocked commands (with the reason and alternative): `monitor` (persistent scheduled jobs/webhooks — poll from your side instead), `feedback`/`search-feedback` (sends usage data, also disabled at the CLI level), `browser`/`launch`/`interact` (persistent remote browser session), `download`/`experimental`/`x` (writes to local disk — use `crawl`), `parse` (needs a local file — `scrape` the file's URL instead), `config`/`login`/`logout` (server holds the API key), `init`/`setup`/`make`/`env`/`doctor` (local-machine only).

Blocked flags: `-k/--api-key`, `--api-url` (server-managed credentials), `-o/--output` (read `stdout` from the response), `--schema-file`/`--actions-file`/`--scrape-options-file` (use inline JSON), `--webhook` (no outbound webhooks), `--profile`/`--no-save-changes` (no persistent browser state).

A blocked call raises a tool error whose message names the alternative — read it, don't just retry.

**Reading the response**: always `exit_code` (0 or 1 — the CLI has no richer taxonomy), `stdout`, `stderr`, `timed_out`. Sometimes also: `truncated` + `original_bytes` (output was cut at 10MiB, not discarded — narrow with `--limit`/a single `--format` and retry); `hint` on truncation/timeout with a concrete next step; `update_available` with a version string when a newer CLI release exists on the server — mention it to the user, don't try to upgrade it yourself (that's a server-admin action via `scripts/update-cli.sh`).

**Reading stderr** (the CLI only has 0/1 exit codes — the actionable detail is always in the text, wording varies by endpoint): a 3-digit HTTP code or words like "unauthorized"/"invalid token" → bad server-side key, report it, don't retry; "credit"/402 → out of credits, stop retrying; "rate limit"/429 → back off and retry once; 5xx or a network error → back off and retry once; anything else is almost always a bad argument — fix it per the message and retry.
