# Exa MCP — Agent guide

## 1. Connect

Add `https://{your-domain}/mcp` to an OAuth-capable MCP client and complete GitHub login. Your login must be in `EXA_MCP_GITHUB_USERS`; a non-allowlisted login receives no tools. Deployment instructions are in [DEPLOYMENT.md](../DEPLOYMENT.md).

Clients without interactive OAuth may use an administrator-issued static bearer token in an `Authorization` header. Static tokens use the same login allowlist. Client capabilities vary by version; do not assume a named client always lacks OAuth.

## 2. Use the upstream schema

This service proxies [Exa's hosted MCP](https://exa.ai/docs/reference/exa-mcp). Read the current `tools/list` schemas for parameters and results. The local default tool selection is `web_search_exa`, `web_fetch_exa`, `web_search_advanced_exa`, and `agent_run`; administrators can change it through `EXA_MCP_UPSTREAM_TOOLS`.

Use search for discovery, fetch for known URLs, advanced search for required filters, and an agent run for multi-step research. API credentials stay on the server; never put them in tool arguments. Calls remain subject to the server account's quota and upstream rate limits.

## 3. Long runs and errors

The local upstream timeout is 800 seconds and Nginx's read timeout is 900 seconds. If a tool returns a running status and run ID, resume using its current input schema. A transport timeout alone does not prove the upstream job stopped; inspect an existing run before starting another.

- `server is at capacity`: nothing executed locally; retry after a short delay.
- Empty `tools/list`: check the GitHub login allowlist and configured tool selection.
- `401` or an invalid-key error: distinguish the local OAuth／bearer token from the server's Exa API Key; report the failing layer.
- `402` or an exhausted-quota error: resolve the account quota before retrying.
- `/register` returning `429`: local registration rate limit; wait before reconnecting.

Token management, API Key verification and callback allowlists are documented in [CONFIGURATION.md](CONFIGURATION.md).
