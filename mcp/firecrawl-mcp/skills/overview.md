# Firecrawl MCP reference

Adapted from [firecrawl/cli](https://github.com/firecrawl/cli), ISC-licensed; provenance is recorded in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). These examples target this server's pinned CLI, not every upstream release.

## 1. MCP boundary

Use `firecrawl_cli(args, stdin?)`, with the subcommand at `args[0]`. General connection, output handling and server boundaries are in [AGENTS.md](../AGENTS.md). Supported commands are `search`, `scrape`, `map`, `crawl`, `agent`, `research`, and `credit-usage`.

Remote callers retrieve these references with `firecrawl_skill("search")`, `firecrawl_skill("scrape")`, and the corresponding command name; relative file links are for repository readers.

Calls have a 180-second timeout and 10 MiB output-stream limits. Results return in stdout; server-local file paths, credential overrides, webhooks and persistent browser profiles are blocked. For long crawl／agent jobs, retain the returned ID and poll instead of waiting inside one call.

## 2. Command references

| Task | Reference |
|---|---|
| Discover pages | [search](search.md) |
| Fetch known URLs | [scrape](scrape.md) |
| Locate a subpage | [map](map.md) |
| Collect a site section | [crawl](crawl.md) |
| Structured extraction | [agent](agent.md) |

```jsonl
{"args":["research","search-papers","diffusion image synthesis","--limit","10"]}
{"args":["credit-usage","--json"]}
```

Check current account usage before large jobs; pricing and included allowances come from the upstream account, not a fixed value in this document. Flags and return schemas follow the deployed tool description and CLI help.
