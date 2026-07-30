---
name: a-share-etf-feishu-brief
description: Use when running or scheduling an a-etf-tech-scanner-compatible project to compute T-1 A-share ETF signals and send the fixed-format Feishu brief via Hermes message channel or webhook.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [a-share, etf, feishu, hermes-send, cron, shanghai, signals]
    related_skills: [lark-markdown, stocks]
---

# A-share ETF Feishu Brief

## Overview
This skill runs an `a-etf-tech-scanner`-compatible project, verifies the daily wrapper, and sends the fixed-format Feishu brief for T-1 A-share ETF signals.

This skill is written to be **distributable**:
- do not assume the project lives under `/home/ubuntu/...`
- do not assume a specific Hermes profile, cron job, or local secret file already exists
- require an explicit environment contract before execution
- fail at preflight with a concrete missing-item report instead of guessing paths or silently degrading

The target project must provide a `daily_job.py` wrapper that:
- resolves the latest completed trade day in `Asia/Shanghai`
- runs the ETF scan
- writes summary and Feishu payload artifacts
- supports `--send-via-hermes` for fixed-format Feishu delivery through `hermes send`
- supports webhook sending when `FEISHU_WEBHOOK_URL` or `LARK_WEBHOOK_URL` exists
- has a dedicated `online_enrichment.py` script for NewsNow-first online source collection with cache reuse and retention pruning
- uses the local NewsNow service on `http://127.0.0.1:4444` as the preferred path because it bypasses the public basic-auth front door and directly accesses the same backend on this machine
- treats the public URL (`https://newsnow.tencent.{your-domain}` — replace `{your-domain}` with the actual deployment domain) as a secondary/manual path only when local access is unavailable
- requires an AI synthesis step after NewsNow retrieval (and optional Exa fallback): the agent must read the technical summary plus the collected sources, judge whether the industry/theme is overheated, whether there are sudden catalysts or risks, and then rewrite `outputs/notifications/online_enrichment.json` before final sending

## When to Use
- The user asks to run the ETF brief once end-to-end.
- The user asks to resend the formatted Feishu signal brief.
- The user asks to schedule the project for daily noon execution in Shanghai time.
- The user asks to verify that the project still produces T-1 signal outputs and Feishu-ready artifacts.

Do not use for:
- changing trading logic itself without separately reviewing `etf_scanner.py`
- ad hoc market commentary that is not tied to the project outputs
- generic US-stock / crypto quote lookups (use the `stocks` skill for that)

## Environment Contract

Before using this skill, establish these inputs explicitly:

### Required runtime inputs
- `PROJECT_ROOT`: absolute path to the ETF scanner project root
- `RUN_TZ`: default `Asia/Shanghai`
- `NEWSNOW_BASE_URL`: default `http://127.0.0.1:4444`
- `NEWSNOW_SOURCES`: comma-separated source IDs; default `36kr-quick,cls-telegraph,ifeng,tencent-hot,thepaper,wallstreetcn-quick,weibo`
- `MAX_PER_DIRECTION`: default `5`

### Required project files under `PROJECT_ROOT`
- `daily_job.py`
- `online_enrichment.py`
- `ai_enrichment_rewriter.py`
- `config.toml`
- `.venv/` (or another explicitly provided runnable environment)

### Required tool/runtime capabilities
- `python` runnable inside the selected environment
- `hermes` CLI available in `PATH` when using `--send-via-hermes`
- network access to NewsNow local service when using NewsNow / hybrid enrichment

### Optional-but-common credentials or delivery config
- `EXA_API_KEY` when Exa fallback is expected
- `FEISHU_WEBHOOK_URL` or `LARK_WEBHOOK_URL` when webhook delivery is expected
- Hermes gateway / home-channel configuration when Hermes message delivery is expected

If any required item is missing, stop at preflight and report the exact missing path, binary, env var, or endpoint.

## Preflight Checks

Run these checks before the first real execution in a new environment, after moving the repo, or when packaging this skill for another machine:

```bash
export PROJECT_ROOT="/absolute/path/to/a-etf-tech-scanner"
test -d "$PROJECT_ROOT"
test -f "$PROJECT_ROOT/daily_job.py"
test -f "$PROJECT_ROOT/online_enrichment.py"
test -f "$PROJECT_ROOT/ai_enrichment_rewriter.py"
test -f "$PROJECT_ROOT/config.toml"
test -d "$PROJECT_ROOT/.venv"
command -v hermes
curl -fsS "$NEWSNOW_BASE_URL/health" || true
```

Completion criterion:
- every required file/path exists
- the runtime environment is activatable
- `hermes` is callable if `--send-via-hermes` will be used
- NewsNow endpoint is reachable when NewsNow / hybrid enrichment is requested

## Project Paths
- Project root: `PROJECT_ROOT`
- Venv: `PROJECT_ROOT/.venv` unless the caller provides another runnable environment
- Daily wrapper: `PROJECT_ROOT/daily_job.py`
- Notification outputs: `PROJECT_ROOT/outputs/notifications/`
- Price cache: `PROJECT_ROOT/data/prices/`
- Enrichment cache: `PROJECT_ROOT/data/enrichment/`

## Canonical Commands

### Test suite
```bash
export PROJECT_ROOT="/absolute/path/to/a-etf-tech-scanner"
cd "$PROJECT_ROOT"
. .venv/bin/activate
pytest -q
```
Completion criterion: pytest exits 0.

### Full end-to-end run via Hermes message channel
```bash
export PROJECT_ROOT="/absolute/path/to/a-etf-tech-scanner"
export NEWSNOW_BASE_URL="${NEWSNOW_BASE_URL:-http://127.0.0.1:4444}"
export NEWSNOW_SOURCES="${NEWSNOW_SOURCES:-36kr-quick,cls-telegraph,ifeng,tencent-hot,thepaper,wallstreetcn-quick,weibo}"
cd "$PROJECT_ROOT"
. .venv/bin/activate
python daily_job.py \
  --run-date "$(date +%F)" \
  --max-per-direction "${MAX_PER_DIRECTION:-5}" \
  --enrichment-provider hybrid \
  --newsnow-base-url "$NEWSNOW_BASE_URL" \
  --newsnow-sources "$NEWSNOW_SOURCES" \
  --send-via-hermes
```
Completion criterion: stdout JSON reports `market_open: true` on a trading day, `focus_buy: 5`, `focus_sell: 5`, `enrichment_generated: true`, `rewrite_result.rewritten > 0`, and `sent_via_hermes: true`.

### Generate NewsNow-first live source material with local service access
Use this only for targeted debugging of the enrichment layer. Normal production and cron runs should call `daily_job.py`, which already chains enrichment + AI rewrite automatically.

```bash
export PROJECT_ROOT="/absolute/path/to/a-etf-tech-scanner"
export NEWSNOW_BASE_URL="${NEWSNOW_BASE_URL:-http://127.0.0.1:4444}"
export NEWSNOW_SOURCES="${NEWSNOW_SOURCES:-36kr-quick,cls-telegraph,ifeng,tencent-hot,thepaper,wallstreetcn-quick,weibo}"
cd "$PROJECT_ROOT"
. .venv/bin/activate
python online_enrichment.py \
  --env-file "${HERMES_ENV_FILE:-$HOME/.hermes/.env}" \
  --targets-file outputs/notifications/latest_enrichment_targets.json \
  --output-file outputs/notifications/online_enrichment.json \
  --provider hybrid \
  --newsnow-base-url "$NEWSNOW_BASE_URL" \
  --newsnow-sources "$NEWSNOW_SOURCES"
```
Completion criterion: stdout reports the real `output_file`, `provider`, `newsnow_base_url`, and `newsnow_sources`; `online_enrichment.json` contains recent industry-news bullets/sources from NewsNow, with Exa only as a fallback when local news hits are insufficient.

## NewsNow Source Policy
Use NewsNow as the primary enrichment source for recent industry/theme news, not for generic fund basics.

The minimum high-quality source set is:
- `36kr-quick`
- `cls-telegraph`
- `ifeng`
- `tencent-hot`
- `thepaper`
- `wallstreetcn-quick`
- `weibo`

Interpretation rules:
1. Prefer `http://127.0.0.1:4444` on this host. It reaches the local backend directly and avoids the basic-auth gate configured on the public Caddy URL.
2. Use the public `https://newsnow.tencent.{your-domain}` only as a fallback/manual inspection path when local access fails and credentials are available.
3. Treat NewsNow titles as recent signal evidence about the ETF's industry/theme; the downstream AI rewrite step must convert them into a coherent judgment about heat, catalysts, divergence risk, and recommendation tone.
4. If NewsNow yields too few relevant hits, supplement with Exa using industry-level news queries rather than generic fund-profile queries.

## Mandatory AI Synthesis Before Send
After the NewsNow-first enrichment step, the agent must read both:
- `outputs/notifications/latest_summary.json`
- `outputs/notifications/online_enrichment.json`

Then, for each Top buy / sell ETF, the agent must use the NewsNow recent-news bullets and any Exa fallback sources to produce the final supplement text instead of sending the raw script-generated summary directly.

The AI synthesis must explicitly judge:
1. whether the tracked industry/theme is already overheated or too crowded;
2. whether there are fresh catalysts, policy changes, earnings/approval/procurement signals, or sudden negative events;
3. whether the news flow confirms the technical signal or creates a divergence risk;
4. whether the final buy/sell conviction should be reinforced, toned down, or flagged as event-driven.

Before final sending, run the programmatic AI rewrite step so `outputs/notifications/online_enrichment.json` reflects the AI's integrated judgment, grounded in the NewsNow recent-news evidence and any Exa fallback sources. Prefer a full `summary`; optional helper fields such as `thesis`, `recent_hotspot`, `recommendation`, `reason`, `risk_note` can be included when useful.

Target writing style for the final supplement text:
- natural Chinese prose written by the AI, without forcing a single rigid template
- no `Summary:` / `摘要：` / `以下是要点摘要` template phrasing
- no ellipsis-based truncation like `…`
- `recommendation` should preserve AI discretion and need not be hard-coded to a fixed phrase

Completion criterion: `online_enrichment.json` contains AI-written final summaries produced by the rewrite step rather than untouched raw NewsNow/Exa script output.

## Fixed Hermes Message Format
The built-in `--send-via-hermes` format is fixed as follows:
- title bolded
- `**买入 Top 5**` and `**卖出 Top 5**` bolded
- each ETF `code｜name` bolded
- scores rendered as `score/100`
- no investment-advice disclaimer line
- no “通过 Hermes 消息通道正式发送到 Feishu” line
- data source fixed to `数据来源：AKShare, BaoStock, 新闻公开页面。`

## Important Output Files
After a successful run, inspect:
- `outputs/notifications/latest_summary.json`
- `outputs/notifications/latest_feishu_post.json`
- `outputs/notifications/latest_enrichment_targets.json`
- `outputs/notifications/online_enrichment.json`
- `outputs/notifications/latest_hermes_send_result.json` when using `--send-via-hermes`
- `outputs/scan_status.json`

## Cache Behavior
Current runtime cache policy in the project is:
- price cache TTL: `48h`
- price cache retention: `30d`
- stale price cache fallback: `7d`
- enrichment cache TTL: `24h`
- enrichment cache retention: `14d`

Important interpretation:
1. The scanner caches raw daily price history per ETF under `data/prices/*.csv`.
2. The scanner still re-traverses the ETF universe and recomputes indicators from the cached history window; it is not a one-bar incremental indicator engine.
3. Because the price cache TTL is now `48h`, daily noon cron runs and same/next-day reruns are much more likely to hit local cache rather than re-fetching the entire price history from upstream.

## Cron Setup
Preferred Hermes cron schedule:
- schedule: `0 12 * * *`
- workdir: `PROJECT_ROOT`
- timezone assumption: `RUN_TZ=Asia/Shanghai` unless the user explicitly overrides it

Cron prompt should:
1. resolve or receive `PROJECT_ROOT` explicitly; never guess a machine-specific absolute path
2. run `daily_job.py` for today at noon semantics
3. stop cleanly on non-trading days
4. pass `--enrichment-provider hybrid`, local `--newsnow-base-url "$NEWSNOW_BASE_URL"`, and the required NewsNow source set directly to `daily_job.py`
5. rely on `daily_job.py` to chain online enrichment + AI rewrite automatically, then inspect the JSON result for `enrichment_generated` and `rewrite_result`
6. use webhook if configured
7. otherwise call the built-in `--send-via-hermes` path

## Reporting Checklist
When reporting a run, include:
- run_id
- run_date
- as_of_date
- rows
- focus_buy / focus_sell
- whether webhook send happened
- whether Hermes send happened
- exact summary / payload / send-result paths

## Common Pitfalls
1. Running without an explicit `PROJECT_ROOT`. A distributable skill must not assume `/home/ubuntu/...`; require a concrete project root first.
2. Skipping preflight in a new environment. Check files, venv, `hermes` CLI, and NewsNow reachability before the first real run.
3. Confusing `run_date` with `as_of_date`. The project reports T-1 completed trading day, not same-day close.
4. Expecting webhook send without env vars. If webhook vars are absent, use `--send-via-hermes`.
5. Treating static enrichment as live web enrichment. `smoke_enrichment.json` is only a fallback sample; production runs should generate `outputs/notifications/online_enrichment.json` through `online_enrichment.py` first.
6. Sending the raw script-generated enrichment summary directly. The NewsNow/Exa step only supplies source material; the agent must read it, judge news heat / abrupt events / confirmation-vs-divergence, and rewrite the final supplement text before send.
7. Misreading cache semantics. The project caches raw history and enrichment payloads, but the scanner still recomputes indicators across the ETF universe each run.
8. Interpreting a foreground timeout as a true task failure. Long all-market scans may outlive a short terminal timeout even when the underlying job later succeeds; verify the real exit status and output artifacts.

## Verification Checklist
- [ ] `PROJECT_ROOT` is explicit and points at the intended repo
- [ ] preflight checks pass in the target environment
- [ ] `pytest -q` passes
- [ ] a full `daily_job.py` run exits 0
- [ ] `latest_summary.json` exists
- [ ] `latest_feishu_post.json` exists
- [ ] `latest_enrichment_targets.json` exists
- [ ] Hermes-send run produced `latest_hermes_send_result.json`
- [ ] run output shows Top 5 buy and Top 5 sell counts
- [ ] `config.toml` reflects the intended cache TTL (`request.cache_hours = 48`)
