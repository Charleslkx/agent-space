# firecrawl search

Web search with optional full-page content extraction. Returns results as JSON.

Trimmed from upstream `firecrawl-search/SKILL.md`. Ignore its `.firecrawl/`, `-o`, `jq`, and `search-feedback` sections — this server returns JSON directly in the response and has search feedback disabled (`FIRECRAWL_NO_SEARCH_FEEDBACK=1`).

## When to use

No specific URL yet: finding pages, answering a question, discovering sources. First step of search → scrape → map → crawl.

## Examples

```
firecrawl_cli(["search", "your query", "--json"])
firecrawl_cli(["search", "your query", "--scrape", "--json"])           # also fetch full page content per result
firecrawl_cli(["search", "your query", "--sources", "news", "--tbs", "qdr:d", "--json"])  # news from the past day
```

## Options

| Option | Description |
|---|---|
| `--limit <n>` | Max results (default 5, max 100) |
| `--sources <web,images,news>` | Source types |
| `--categories <github,research,pdf>` | Filter by category |
| `--tbs <qdr:h\|d\|w\|m>` | Time-based filter (hour/day/week/month) |
| `--location <place>` | Geo-target results |
| `--country <code>` | ISO country code (default US) |
| `--scrape` | Also scrape full content for each result |
| `--scrape-formats <formats>` | Formats when `--scrape` is set (default markdown) |
| `--highlights` / `--no-highlights` | Query-relevant highlights vs. original snippets |
| `--json` | JSON output |

## Tips

- `--scrape` already fetches full content — don't call `scrape` again on the same URLs.
- Parse the JSON response yourself (it's already in the tool result, no file to `jq`): result URLs are under `.data.web[].url` etc.
- Search feedback (`search-feedback`) is a blocked command on this server and disabled at the CLI level — don't try to send it.
