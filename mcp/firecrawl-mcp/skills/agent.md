# firecrawl agent

AI-powered autonomous extraction: the agent navigates a site and returns structured data (can take a couple of minutes on complex sites).

Trimmed from upstream `firecrawl-agent/SKILL.md`. Its `--wait -o ...` pattern does **not** apply here: `--wait` blocks until the run finishes and can exceed this tool's 180s timeout, and `-o` is blocked.

## When to use

You need structured data from a complex, multi-page site and manual scraping would mean navigating many pages yourself. For a single known page, prefer `scrape` — it's faster and cheaper.

## The async pattern (do this, not `--wait`)

```
# 1. Kick off the run, no --wait: returns a job id immediately
firecrawl_cli(["agent", "extract all pricing tiers", "--urls", "https://example.com/pricing", "--max-credits", "20"])

# 2. Poll with the job id from step 1 until it's done
firecrawl_cli(["agent", "<jobId>", "--status"])
```

## Options

| Option | Description |
|---|---|
| `--urls <urls>` | Comma-separated starting URLs |
| `--model <spark-1-mini\|spark-1-pro>` | spark-1-mini is default/cheaper, spark-1-pro is higher accuracy |
| `--schema '<json>'` | JSON schema for structured output, inline only (`--schema-file` is blocked) |
| `--max-credits <n>` | Credit cap for this run — always set this |
| `--status` | Check status of an existing job (pass the job id as the positional) |
| `--cancel` | Cancel an active job (pass the job id as the positional) |

## Tips

- Always pass `--schema` for predictable output — without it the agent returns freeform data.
- Always set `--max-credits`; agent runs cost more than a simple scrape.
- `--webhook` is blocked on this server; poll instead of relying on a callback.
