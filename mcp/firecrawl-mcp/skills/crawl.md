# firecrawl crawl

Bulk extract content from a website, following links up to a depth/page limit.

Trimmed from upstream `firecrawl-crawl/SKILL.md`. Its `--wait -o ...` pattern does **not** apply here: `--wait` blocks until the crawl finishes, which will exceed this tool's 180s timeout on anything but a tiny crawl, and `-o` is blocked.

## When to use

You need content from many pages on a site (e.g. all of `/docs/`). Step 4 of search → scrape → map → crawl.

## The async pattern (do this, not `--wait`)

```
# 1. Kick off the crawl, no --wait: returns a job id immediately
firecrawl_cli(["crawl", "https://example.com/docs", "--include-paths", "/docs", "--limit", "50"])

# 2. Poll with the job id from step 1 until it's done
firecrawl_cli(["crawl", "<jobId>", "--status"])
```

Only use `--wait` for a crawl you're confident will finish in well under 180s (very small `--limit`, e.g. 5-10 pages).

## Options

| Option | Description |
|---|---|
| `--limit <n>` | Max pages to crawl |
| `--max-depth <n>` | Max link depth to follow |
| `--include-paths <paths>` / `--exclude-paths <paths>` | Comma-separated path filters |
| `--crawl-entire-domain` | Crawl the entire domain |
| `--allow-subdomains` | Include subdomains |
| `--delay <ms>` | Delay between requests |
| `--max-concurrency <n>` | Max parallel crawl workers |
| `--scrape-options '<json>'` | Per-page scrape options, inline JSON only (`--scrape-options-file` is blocked) |
| `--status` | Check status of an existing job (pass the job id as the positional) |
| `--cancel` | Cancel an active job (pass the job id as the positional) |

## Tips

- Scope with `--include-paths`/`--max-depth` — don't crawl an entire site when you only need one section.
- Billed per page. Check `firecrawl_cli(["credit-usage"])` before a large crawl.
- `--webhook` is blocked on this server; poll instead of relying on a callback.
