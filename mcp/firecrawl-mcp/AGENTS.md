# Firecrawl CLI MCP — Agent guide

## 1. Connect and choose a command

Connect an OAuth-capable client to `https://{your-domain}/mcp` and complete GitHub login. Your login must be in `FIRECRAWL_MCP_GITHUB_USERS`; otherwise tools are filtered out. Deployment is documented in [DEPLOYMENT.md](../DEPLOYMENT.md).

`firecrawl_cli(args, stdin?)` executes the server CLI. `firecrawl_skill(name)` provides optional command examples. Use the current tool description or the installed CLI's help for version-matched flags.

| Need | Command |
|---|---|
| Discover pages | `search` |
| Read known URLs | `scrape` |
| Find a subpage | `map --search`, then `scrape` |
| Read a site section | `crawl` |
| Structured multi-page extraction | `agent` |
| Paper or GitHub research | `research` |
| Check quota | `credit-usage` |

## 2. Arguments and server boundaries

- `args` starts with the subcommand, excludes `firecrawl`, and is an array, not a shell string. Example: `{"args":["search","your query","--limit","5"]}`.
- Put flags after the command. Pass each URL as a separate element; do not add shell quote characters.
- Use inline JSON or stdin. `-o/--output`, `*-file` inputs, server-local files, credential overrides, webhooks and persistent browser profiles are blocked.
- Only the commands above are exposed; server configuration, local project initialization, scheduled monitoring and feedback are unavailable.
- Read results from stdout. For long `crawl`／`agent` jobs, submit without `--wait`, retain the returned job ID, then poll with `["crawl","<jobId>","--status"]` or the corresponding agent command. Calls time out after 180 seconds.

## 3. Inspect results

Check `exit_code`, stdout, stderr and `timed_out`. `truncated`／`original_bytes` means output exceeded the 10 MiB stream limit; narrow the request. A CLI failure is returned in the response and may not be an MCP protocol error.

`server is at capacity` means nothing ran. Invalid credentials or exhausted credits require server-admin action. For transient 429／5xx／network failures, retry with backoff only after checking whether a job was already created. Report `update_available`; CLI upgrades are administrator operations.

Detailed restrictions and authentication configuration are in [CONFIGURATION.md](CONFIGURATION.md).
