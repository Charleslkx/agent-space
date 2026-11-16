# Firecrawl crawl

Use for a bounded site section. Scoping and limits avoid collecting unrelated pages.

```jsonl
{"args":["crawl","https://example.com/docs","--include-paths","/docs","--limit","50"]}
{"args":["crawl","<returned-job-id>","--status"]}
```

Submit without `--wait` for jobs that may exceed the server's 180-second call timeout. Retain the job ID and poll for completion; do not start duplicate jobs after a timeout without checking the original.

`--scrape-options` accepts inline JSON; local files, output paths and webhook callbacks are blocked. Check account usage before large runs. Depth, concurrency and cancellation flags follow the deployed tool description.
