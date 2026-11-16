# Firecrawl search

Use when no target URL is known. Shared server boundaries are in [overview.md](overview.md).

```jsonl
{"args":["search","your query","--limit","5","--json"]}
{"args":["search","your query","--scrape","--json"]}
{"args":["search","your query","--sources","news","--tbs","qdr:d","--json"]}
```

`--scrape` includes fetched content, so inspect it before scraping the same URLs again. Results are returned in stdout; feedback and file-output commands are unavailable. Available source filters and output shapes follow the deployed tool description.
