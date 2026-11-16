# Firecrawl map

Use to locate a subpage or inspect URLs before fetching a site section.

```jsonl
{"args":["map","https://docs.example.com","--search","authentication"]}
{"args":["map","https://example.com","--limit","500","--json"]}
```

For a single subpage, map with a search term and then scrape the returned URL. Reserve crawl for collecting multiple pages. Results return in stdout; sitemap and subdomain options follow the deployed tool description.
