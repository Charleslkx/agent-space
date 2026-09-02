# Exa MCP — agent guide

Everything an agent needs to connect to this MCP and call it correctly. This file can be copied whole into another agent project's context.

This server is a **passthrough proxy** for Exa's official hosted MCP. Tool names, parameters, and responses are forwarded verbatim from `https://mcp.exa.ai/mcp`, so **Exa's own documentation at <https://exa.ai/docs/reference/exa-mcp> is the reference** — there is no local tool dialect to learn. This file only covers what is specific to this deployment.

## 1. Connect

Clients that support MCP OAuth just need the URL. The first call opens a browser for GitHub login.

```bash
claude mcp add --transport http exa https://{your-domain}/mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.exa]
url = "https://{your-domain}/mcp"
```

Cursor / Windsurf / Zed / VS Code / Gemini CLI, and the ChatGPT / Claude.ai / Grok remote-connector UIs: add the same URL. Callback URLs for all of these are already allowlisted server-side.

**Trae is the exception.** Its MCP client cannot perform OAuth and cannot refresh tokens — it only supports static headers. Ask the server admin to run `scripts/apikey.sh token add <your-github-login>`, then:

```json
{
  "mcpServers": {
    "exa": {
      "url": "https://{your-domain}/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Either way your GitHub login must be on the server's allowlist (`EXA_MCP_GITHUB_USERS`). If it isn't, the tools simply **won't appear in `tools/list`** — you get an empty list, not an explicit "denied" error. A static token grants exactly the same access as OAuth, no more.

## 2. Tools

Whatever `tools/list` returns is what upstream currently offers; don't hardcode this set. As configured, that is:

| Tool | Use it for |
|---|---|
| `web_search_exa` | Default. Search the web and get clean, ready-to-use content back |
| `web_fetch_exa` | You already have one or more URLs and want their full content as markdown |
| `web_search_advanced_exa` | You need precise control: category/domain filters, date ranges, text constraints, geo-targeting, highlights, summaries, subpage crawling |
| `agent_run` | Multi-step research, list building, enrichment, or structured output — anything that needs more than a single search |

Reach for `web_search_exa` first. Escalate to `web_search_advanced_exa` only when a filter you actually need is missing ("papers on arxiv.org from the last year", "news excluding site X", "crawl this site's docs subpages"). Escalate to `agent_run` only when one search genuinely cannot answer the question.

## 3. Never pass an API key

This deployment holds the Exa API key server-side. Do not pass a key, a token, or an `x-api-key` in tool arguments — there is no parameter for it, and none is needed. This is the whole point of the deployment: calls bill to the server's own key rather than the anonymous free tier, so you should not see the tier-limit 429s that the stock connectors hit.

## 4. Long `agent_run` calls

`agent_run` runs the entire agent loop inside one call, which can take many minutes. This server allows ~800s and the proxy in front of it ~900s.

If a run outlives the call window, `agent_run` returns `status: "running"` with the run's `id` **instead of an error** — the run keeps executing on Exa's side. Call `agent_run` again with `runId` set to that `id` to keep waiting. Don't restart the query from scratch; you'll pay for the work twice.

To refine or extend a finished run, pass `previousRunId` rather than re-running, and use `input.exclusion` to avoid resurfacing results you already have.

`agent_run` is billed per run, so prefer a search tool when a search would do.

## 5. Errors specific to this deployment

Most errors come straight from Exa and mean what Exa's docs say they mean. These three are added by this server:

- **`server is at capacity (N concurrent Exa calls); nothing ran, retry in a few seconds`** — a local concurrency cap, not an Exa rate limit. Nothing executed and nothing was billed. Wait a few seconds and retry the identical call; don't rewrite the query.
- **Empty `tools/list` after a successful login** — your GitHub login isn't on the allowlist. Report it; retrying won't help.
- **`401` on every request from Trae** — the static token is missing, wrong, or was rotated. Ask the admin for a new one; OAuth is not an option in Trae.

A `429` on `/register` (not on a tool call) is the OAuth dynamic-client-registration rate limit. A normal client registers once per install, so hitting it means something is re-registering in a loop.
