# Firecrawl scrape

Use for known URLs; use [search](search.md) or [map](map.md) for discovery.

```jsonl
{"args":["scrape","https://example.com"]}
{"args":["scrape","https://example.com","--only-main-content"]}
{"args":["scrape","https://example.com","--wait-for","3000"]}
{"args":["scrape","https://example.com","--format","markdown,links","--json"]}
```

Pass each URL as a separate element. JSON extraction schemas and browser actions must be inline strings (`--schema`, `--actions`), not local files. Persistent profiles and `-o/--output` are blocked. Choose only the formats needed; consult the deployed tool description for additional flags.
