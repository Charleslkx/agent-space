# Firecrawl agent

Use for structured multi-page extraction; a known single page usually needs only scrape.

```jsonl
{"args":["agent","extract all pricing tiers","--urls","https://example.com/pricing","--max-credits","20"]}
{"args":["agent","<returned-job-id>","--status"]}
```

Bound the run with `--max-credits` and an inline `--schema` when structured output is needed. Submit long jobs without `--wait`; retain the job ID for polling instead of restarting extraction.

Model names, defaults and supported flags follow the deployed tool description. `--schema-file`, `-o/--output` and webhooks are blocked on this server.
