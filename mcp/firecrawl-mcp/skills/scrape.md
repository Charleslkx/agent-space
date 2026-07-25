# firecrawl scrape

Scrape one or more URLs. Returns clean, LLM-optimized markdown (or other formats).

Trimmed from upstream `firecrawl-scrape/SKILL.md`. Ignore its `.firecrawl/` / `-o` sections — the page content comes back directly in the tool response's `stdout` field.

## When to use

You have a specific URL and want its content; the page is static or JS-rendered (SPA). Step 2 of search → scrape → map → crawl.

## Examples

```
firecrawl_cli(["scrape", "https://example.com"])
firecrawl_cli(["scrape", "https://example.com", "--only-main-content"])
firecrawl_cli(["scrape", "https://example.com", "--wait-for", "3000"])           # wait for JS to render
firecrawl_cli(["scrape", "https://a.com", "https://b.com", "https://c.com"])     # multiple URLs, scraped concurrently
firecrawl_cli(["scrape", "https://example.com", "--format", "markdown,links", "--json"])
firecrawl_cli(["scrape", "https://example.com/pricing", "--query", "What is the enterprise plan price?"])
```

## Options

| Option | Description |
|---|---|
| `-f, --format <formats>` | Comma-separated: markdown, html, rawHtml, links, screenshot, json |
| `-Q, --query <prompt>` | Ask a question about the page (5 extra credits) |
| `--only-main-content` | Strip nav/footer/sidebar |
| `--wait-for <ms>` | Wait for JS rendering before scraping |
| `--include-tags <tags>` / `--exclude-tags <tags>` | Comma-separated HTML tags |
| `--redact-pii` | Redact personally identifiable information |
| `--schema '<json>'` | JSON schema for structured extraction (inline only — `--schema-file` is blocked) |
| `--actions '<json>'` | JSON actions array to run during scrape (inline only — `--actions-file` is blocked) |
| `--max-age <ms>` | Max age of cached content |
| `--country <code>` / `--languages <codes>` | Geo/language targeting |
| `--json` | JSON output (required when using multiple `--format` values) |

## Tips

- Prefer plain scrape over `-Q/--query` — read the returned markdown yourself; reserve `--query` for a single targeted answer.
- Single format returns raw content; multiple formats (`--format markdown,links`) return JSON.
- Always pass the URL as its own array element — never build a shell string.
- `--profile`/`--no-save-changes` (persistent browser profile) are blocked on this server.
