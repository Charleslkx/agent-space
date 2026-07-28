---
name: feishu-base-autumn-jobs-monitor
description: Use when the user wants to monitor campus recruitment openings from campus.sma-wiki.cn, query a specific date, exclude internship batches, and notify through Hermes/Feishu on demand or by cron.
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
created_by: agent
metadata:
  hermes:
    tags: [feishu, campus, crawl, cron, notifications, hermes-send, autumn-jobs]
    related_skills: [hermes-feishu-automation, scheduled-notification-pipelines]
---

# Campus Jobs Monitor

Monitor campus recruitment data from `campus.sma-wiki.cn`, query a caller-specified date (or default to today in `Asia/Shanghai`), exclude rows whose `batch` contains `实习`, and send a concise notification through Hermes's Feishu channel.

The message begins with a markdown hyperlink to the page-level data source. If the primary URL fails and a new URL is resolved from nowcoder, the message should switch that hyperlink to the new source automatically. Each company line should render the company name as a markdown hyperlink, preferring `appLink` and falling back to `sourceLink`, and should not append a separate source hyperlink.

## When to Use

Use this skill when the user asks to:
- check the campus recruitment page for today's new openings,
- test a historical date such as `2026-07-15`,
- send the current result to Feishu via `hermes send`,
- create, verify, or troubleshoot the daily 18:00 scheduled monitor,
- package or re-install the monitor cron on another machine.

Do not use this skill for:
- arbitrary web scraping tasks,
- editing the campus.sma-wiki.cn data,
- webhook/card payload design,
- non-Feishu notification channels.

## Data Source

Primary URL: `https://campus.sma-wiki.cn/campus/campus_recruit.html?channel=zpdt`

The page embeds all records in a `const RAW_DATA = [...]` JSON array sorted by `fullDate` descending. The notifier scans the full dataset for rows whose `fullDate` matches the resolved target date, then excludes rows whose `batch` contains `实习`.

**Failover**: If the primary URL is unavailable, the script resolves the latest URL from `https://www.nowcoder.com/jobs/recommend/campus` (see `window.__INITIAL_STATE__` → `app.108.recommandCompany.activitys[]` → `companyId=32020` → decoded `url` parameter).

**Monitor fields** used:
- `fullDate` → 开始时间
- `company` → 公司
- `appLink` → 网申链接（优先作为公司名超链接）
- `sourceLink` → 信息源链接（appLink 缺失时回退）

## Bundled Files

This skill bundle ships with:
- `SKILL.md`
- `scripts/feishu_base_autumn_jobs_notify.py`
- `scripts/parse_campus_recruit.py`
- `scripts/install_to_hermes.sh`
- `references/current-cron-job.json`

## Environment Contract

Required runtime tools:
- `python3`
- `hermes`

Required auth/config:
- Hermes must be able to send to Feishu through `hermes send --to feishu`

Optional env vars:
- positional arg supports `2026-07-25`, `2026/7/25`, `20260725`, `7.25`, `7月25日`
- `FORCE_DATE=...` is a backward-compatible fallback when no CLI date arg is passed and accepts the same tolerant formats
- `DRY_RUN=1` to skip live sending
- `HERMES_AUTUMN_JOBS_OUTPUT_DIR=/custom/path` to override the output artifact directory

## Install on a Machine

From the skill directory:

```bash
bash <skill-dir>/scripts/install_to_hermes.sh
```

What it does:
1. creates `~/.hermes/scripts/`
2. copies `feishu_base_autumn_jobs_notify.py` there
3. marks it executable

Installed script path:
- `~/.hermes/scripts/feishu_base_autumn_jobs_notify.py`

## Manual Run Commands

### Real send for today

```bash
python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
```

Completion criterion:
- script exits 0
- output JSON has `ok: true`
- `send_result.success` is true

### Historical positive-branch test

```bash
python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py 2026-07-15
```

Use this to prove the positive branch really sends a company list.

### Dry-run without sending Feishu message

```bash
DRY_RUN=1 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py 2026-07-15
```

Completion criterion:
- output JSON has `send_result.dry_run: true`

Backward-compatible legacy invocation is still accepted:

```bash
FORCE_DATE=2026-07-15 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
```

## Notification Rule

1. Work in `Asia/Shanghai`.
2. Resolve the target date from CLI arg, then `FORCE_DATE`, then today.
3. Scan the dataset for rows whose `fullDate == target_date`.
4. Exclude any row whose `batch` contains `实习`.
5. Send the remaining `公司` list, with `批次` text and only the company name hyperlinked to the application URL.
6. If nothing remains after filtering, send `当日没有新增秋招的公司（已过滤批次中包含“实习”的记录）。`

## Cron Installation Pattern

This workflow is best scheduled as an agent-driven cron job.

Target shape:
- job name: `feishu-base-autumn-jobs-daily`
- schedule: `0 18 * * *`
- `no_agent: false`
- enable `terminal` + `file` toolsets
- have the agent run the notifier in `DRY_RUN=1`, inspect the JSON result, note whether the effective `source_url` changed, and then send the final Feishu message itself
- CLI sessions should usually keep `deliver=local` because the agent itself already sends to Feishu

When using Hermes's cronjob tool, create/update the job with a self-contained prompt that explicitly tells the agent to inspect `source_url`, preserve the page-level source hyperlink, and then send via `hermes send --to feishu`.

Reference payload for the current installed job is included in:
- `references/current-cron-job.json`

## Delivery Path

Delivery is through Hermes's configured Feishu channel, not a webhook.

The script sends via:

```bash
hermes send --to feishu --file - --json
```

## Verification Workflow

When changing this monitor, verify both branches.

### Negative branch
Run with a date that has no non-`实习` rows, or a date absent from the dataset.

Check:
- `company_count == 0`
- message contains `当日没有新增秋招的公司。`
- `send_result.success == true` for real send, or `dry_run == true` for dry-run

### Positive branch
Run with a known matching date such as `2026-07-25`.

Check:
- `company_count > 0`
- every returned item has `batch` not containing `实习`
- message begins with `以下公司是当日开始秋招：`
- message shows only company-name hyperlinks plus optional `批次` text (no separate `信息源` field)
- `send_result.success == true` for real send, or `dry_run == true` for dry-run

### Artifact check
After any run, inspect the result file written under:
- `${HERMES_AUTUMN_JOBS_OUTPUT_DIR:-~/.hermes/outputs/feishu-base-autumn-jobs}/latest_result.json`

Verify that it records:
- `target_date`
- `first_row_date`
- `companies`
- `company_count`
- `scanned_rows`
- `message`
- `send_result`
- `source_url`

## Common Pitfalls

1. Do not include rows whose `batch` contains `实习`; the monitor intentionally excludes them.
2. Do not claim a send succeeded without checking `send_result.success`.
3. Do not rewrite the delivery path to webhook unless the user explicitly changes the requirement.
4. Do not forget the timezone assumption: this workflow is defined in `Asia/Shanghai`.
5. Some source/app links arrive as messy concatenated strings; extract the first valid URL before formatting markdown.

## Recovery Steps

If the monitor stops working:
1. run `hermes status --all` and confirm Feishu is still configured
2. run the script manually with `DRY_RUN=1` to separate logic from delivery
3. run the script without `DRY_RUN` to test live delivery
4. inspect the latest result JSON artifact
5. if the schedule is suspect, list cron jobs and inspect the `feishu-base-autumn-jobs-daily` job
6. check if the campus.sma-wiki.cn URL has changed; if so, the script will automatically attempt to resolve the new URL from nowcoder

## Verification Checklist

- [ ] `hermes status --all` shows Feishu configured
- [ ] Manual real-send run succeeds
- [ ] Historical positive-branch test succeeds (e.g. `python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py 2026-07-25`)
- [ ] Negative-branch test succeeds (e.g. `FORCE_DATE=2026-01-01`)
- [ ] `latest_result.json` contains the expected fields
- [ ] Cron job exists and is configured as the agent-driven `feishu-base-autumn-jobs-daily` workflow

## Campus Recruit Page Parser

`scripts/parse_campus_recruit.py` fetches and parses the 27届校招信息汇总 page.

### Data Source

Primary URL: `https://campus.sma-wiki.cn/campus/campus_recruit.html?channel=zpdt`

This URL is sourced from the nowcoder jobs page (`https://www.nowcoder.com/jobs/recommend/campus`). On the nowcoder page, the link is embedded in `window.__INITIAL_STATE__` → `app.108.recommandCompany.activitys[]` as a `/jump` redirect URL with `companyId=32020`. The parser can automatically resolve the latest URL from nowcoder when the primary URL is unavailable.

### Recognized Fields (18 fields)

| Field | CN Name | Type |
|---|---|---|
| `updateDate` | 更新日期 | str |
| `fullDate` | 完整日期 | str |
| `month` | 月份 | str |
| `company` | 公司名称 | str |
| `batch` | 批次 | str |
| `location` | 工作地点 | str |
| `positions` | 招聘岗位 | str |
| `sourceLink` | 信息源链接 | str |
| `linkSource` | 信息来源标签 | str |
| `appLink` | 网申链接 | str |
| `isKey` | 是否重点公司 | bool |
| `evaluation` | 公司简介 | str |
| `industry` | 行业 | str |
| `nature` | 公司性质 | str |
| `deadline` | 截止日期 | str |
| `isCommercial` | 是否商业推广 | bool |
| `commercialExpiry` | 商业推广过期 | str |
| `isFeaturedCard` | 是否精选卡片 | bool |

### Usage

```bash
python3 scripts/parse_campus_recruit.py                    # fetch live page, field inventory + stats + smoke test
python3 scripts/parse_campus_recruit.py --json             # output raw JSON records
python3 scripts/parse_campus_recruit.py --file cached.html # parse a local cached file
python3 scripts/parse_campus_recruit.py --no-notify        # skip Feishu notification on failure
```

### Retry & Failover Behaviour

1. **Direct fetch** — attempts the primary URL up to 3 times (1 initial + 2 retries).
2. **Exponential backoff** — between retries: 5s, then 10s (`base × factor^attempt` = `5 × 2^attempt`).
3. **Failover** — if all 3 direct attempts fail, fetches the nowcoder page to resolve the latest target URL from `__INITIAL_STATE__`, then retries the new URL with the same 3-attempt strategy.
4. **Feishu notification** — if both direct and failover paths are exhausted, a failure notification is sent via `hermes send --to feishu` detailing every error (type + message) from each attempt. Suppress with `--no-notify` or `DRY_RUN=1`.
5. **Parse failure** — if the HTML is fetched but `RAW_DATA` extraction fails, a notification is also sent unless `--file` mode is used.

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Smoke test failed |
| 2 | `--file` path missing |
| 3 | Fetch exhausted (all retries + failover failed) |
| 4 | HTML parsed but data extraction failed |
