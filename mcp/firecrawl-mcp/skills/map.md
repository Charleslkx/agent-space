# firecrawl map

Discover URLs on a site. Use `--search` to find one specific page within a large site.

Trimmed from upstream `firecrawl-map/SKILL.md`. Ignore its `-o` section — the URL list comes back directly in the tool response.

## When to use

You need to find a specific subpage on a large site, or want the full URL list before scraping/crawling. Step 3 of search → scrape → map → crawl.

## Examples

```
firecrawl_cli(["map", "https://docs.example.com", "--search", "authentication"])
firecrawl_cli(["map", "https://example.com", "--limit", "500", "--json"])
```

## Options

| Option | Description |
|---|---|
| `--search <query>` | Filter URLs by query |
| `--limit <n>` | Max URLs to return |
| `--sitemap <only\|include\|skip>` | Sitemap handling strategy |
| `--include-subdomains` | Include subdomain URLs |
| `--json` | JSON output |

## Tips

Map + scrape is the common pattern: `map --search "auth"` finds `/docs/api/authentication`, then `scrape` that URL directly — cheaper than crawling the whole site when you only need one page.
