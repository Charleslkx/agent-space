---
name: feishu-base-autumn-jobs-monitor
description: Use when the user wants to monitor campus recruitment openings from campus.sma-wiki.cn, detect same-day openings from the top contiguous rows, and notify through Hermes/Feishu on demand or by cron.
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

Monitor campus recruitment data from `campus.sma-wiki.cn`, detect whether the top rows' `fullDate` is today in `Asia/Shanghai`, and send a concise notification through Hermes's Feishu channel.

The message should render each company name as a markdown hyperlink, preferring `appLink` and falling back to `sourceLink`.

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

The page embeds all records in a `const RAW_DATA = [...]` JSON array sorted by `fullDate` descending. The notifier reads the top contiguous block of rows matching today's date.

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
- `FORCE_DATE=YYYY-MM-DD` to test a historical date
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
FORCE_DATE=2026-07-15 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
```

Use this to prove the positive branch really sends a company list.

### Dry-run without sending Feishu message

```bash
FORCE_DATE=2026-07-15 DRY_RUN=1 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
```

Completion criterion:
- output JSON has `send_result.dry_run: true`

## Notification Rule

1. Work in `Asia/Shanghai`.
2. Read the view in its current sort order.
3. Inspect the first row's `开始时间`.
4. If that date is not today:
   - stop scanning immediately
   - send `当日没有新增秋招的公司。`
5. If that date is today:
   - continue reading downward
   - collect every consecutive row whose `开始时间` is today
   - stop at the first row whose `开始时间` is not today
   - send the collected `公司` list

This is intentionally not a full-table scan. It depends on the view already being sorted so that today's openings appear contiguously at the top.

## Cron Installation Pattern

This workflow is best scheduled as a script-only cron job.

Target shape:
- job name: `feishu-base-autumn-jobs-daily`
- schedule: `0 18 * * *`
- script: `feishu_base_autumn_jobs_notify.py`
- `no_agent: true`
- CLI sessions should usually keep `deliver=local` because the script itself already sends to Feishu

When using Hermes's cronjob tool, create/update the job in this shape rather than wrapping the script in an LLM prompt.

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
Run with today's real date when the first row is not today, or choose another date guaranteed not to match the first row.

Check:
- `company_count == 0`
- message contains `当日没有新增秋招的公司。`
- `send_result.success == true` for real send, or `dry_run == true` for dry-run

### Positive branch
Run with a known matching date such as `2026-07-15`.

Check:
- `first_row_date == target_date`
- `company_count > 0`
- `scanned_rows >= company_count`
- message begins with `以下公司是当日开始秋招：`
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

1. Do not scan the entire table for matching dates. This monitor is top-of-view only by design.
2. Do not assume all rows with the same date across the table should be included; only the contiguous top block matters.
3. Do not claim a send succeeded without checking `send_result.success`.
4. Do not rewrite the delivery path to webhook unless the user explicitly changes the requirement.
5. Do not forget the timezone assumption: this workflow is defined in `Asia/Shanghai`.
6. If the view sort changes, the monitor semantics may break even if the script still runs successfully.

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
- [ ] Historical positive-branch test succeeds (e.g. `FORCE_DATE=2026-07-15`)
- [ ] Negative-branch test succeeds (e.g. `FORCE_DATE=2026-01-01`)
- [ ] `latest_result.json` contains the expected fields
- [ ] Cron job exists and points to `feishu_base_autumn_jobs_notify.py`

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
