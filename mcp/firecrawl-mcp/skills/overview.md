# firecrawl (via this MCP)

Trimmed, remote-server version of Firecrawl's own CLI skills (upstream: [firecrawl/cli](https://github.com/firecrawl/cli), ISC license, `skills/firecrawl-cli/SKILL.md` and friends). Call through the `firecrawl_cli(args, stdin?)` tool, not a local `firecrawl` binary — `args` is the argument array after `firecrawl`, no shell string.

## MCP boundary

This server exposes a fixed subset of the CLI over a shared, credential-holding connection:

- **Allowed commands**: `search`, `scrape`, `map`, `crawl`, `agent`, `research`, `credit-usage`.
- **Blocked commands** (with reason): `monitor` (persistent scheduled jobs + webhooks/email), `feedback`/`search-feedback` (sends usage data — this server also sets `FIRECRAWL_NO_SEARCH_FEEDBACK=1`/`FIRECRAWL_NO_ENDPOINT_FEEDBACK=1`, so don't bother sending it), `browser`/`launch`/`interact` (persistent remote browser session), `download`/`experimental`/`x` (writes to local disk), `parse` (needs a local file), `config`/`view-config`/`login`/`logout` (the server holds the API key), `init`/`setup`/`make`/`env`/`doctor` (local-machine only).
- **Blocked flags**: `-k/--api-key`, `--api-url` (server-managed credentials), `-o/--output` (results come back via the tool response's `stdout` field — there is no shared filesystem between this server and you), `--schema-file`/`--actions-file`/`--scrape-options-file` (pass inline JSON instead), `--webhook` (no outbound webhooks), `--profile`/`--no-save-changes` (no persistent browser state).
- **Limits**: 128 args, 16KiB per arg, 1MiB stdin, 10MiB stdout/stderr each (truncated with `truncated: true` if exceeded, not discarded), 180s per call.
- **No `.firecrawl/` directory, no `-o`, no `jq` on files.** Everything the upstream skills describe as "write to `.firecrawl/` then grep/jq" instead comes back directly in the tool response; read it from there. Use `--limit`/`--format` to keep it small instead of writing to disk.
- **No `--wait` on `crawl`/`agent`.** This tool times out at 180s. Call once without `--wait` to get a job id, then poll with `["crawl", "<jobId>", "--status"]` (or `["agent", "<jobId>", "--status"]`).

## Workflow escalation (unchanged from upstream)

1. **search** — no specific URL yet; find pages or answer a question.
2. **scrape** — you have a URL; get its content directly.
3. **map + scrape** — large site, need a specific subpage: `map --search` to find the URL, then `scrape` it.
4. **crawl** — need bulk content from a whole site section (e.g. all of `/docs/`).
5. **agent** — need AI-driven structured extraction from a complex, multi-page site.

Deeper reference for each: `firecrawl_skill("search")`, `firecrawl_skill("scrape")`, `firecrawl_skill("map")`, `firecrawl_skill("crawl")`, `firecrawl_skill("agent")`.

## research (arXiv / GitHub)

Nested subcommand, not in the table above because it's rarely needed: `research search-papers "<query>"`, `research inspect-paper <arxivId>`, `research related-papers <arxivId> --intent "..."`, `research read-paper <arxivId> --question "..."`, `research search-github "<query>"`. All read-only, same stdout-only rule applies (`-o` is blocked here too).

```
firecrawl_cli(["research", "search-papers", "diffusion image synthesis", "--limit", "10"])
```

## Credits

```
firecrawl_cli(["credit-usage", "--json"])
```

`search` costs 2 credits/call; `crawl` is billed per page; `agent` is billed per run, cap it with `--max-credits`.
