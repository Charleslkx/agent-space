---
name: feishu-base-autumn-jobs-monitor
description: Use when the user wants to monitor a fixed Feishu Base autumn-jobs view, detect same-day openings from the top contiguous rows, and notify through Hermes/Feishu on demand or by cron.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
created_by: agent
metadata:
  hermes:
    tags: [feishu, lark, base, cron, notifications, hermes-send, autumn-jobs]
    related_skills: [hermes-feishu-automation, scheduled-notification-pipelines]
---

# Feishu Base Autumn Jobs Monitor

Monitor one fixed Feishu Base view, detect whether the top rows represent companies whose `开始时间` is today in `Asia/Shanghai`, and send a concise notification through Hermes's Feishu channel.

This skill packages the current production 校招 cron as a reusable skill bundle, including the notifier script.

## When to Use

Use this skill when the user asks to:
- check the known Feishu Base autumn-jobs view manually,
- test a historical date such as `2026-07-11`,
- send the current result to Feishu via `hermes send`,
- create, verify, or troubleshoot the daily 18:00 scheduled monitor,
- package or re-install the current 校招 cron on another machine.

Do not use this skill for:
- arbitrary Feishu Base exploration outside this fixed view,
- editing Base records,
- webhook/card payload design,
- non-Feishu notification channels.

## Fixed Resource Coordinates

This workflow is pinned to this Base view:
- URL: `https://my.feishu.cn/base/QupsbMixhaDKiqsc1CTcjJlGnGe?table=tblyww7RWoFyBq2I&view=vewlapetfU`
- `base_token`: `QupsbMixhaDKiqsc1CTcjJlGnGe`
- `table_id`: `tblyww7RWoFyBq2I`
- `view_id`: `vewlapetfU`

Expected visible fields are broader, but the monitor only needs:
- `公司`
- `开始时间`

## Bundled Files

This skill bundle ships with:
- `SKILL.md`
- `scripts/feishu_base_autumn_jobs_notify.py`
- `scripts/install_to_hermes.sh`
- `references/current-cron-job.json`

## Environment Contract

Required runtime tools:
- `python3`
- `lark-cli`
- `hermes`

Required auth/config:
- `lark-cli` must be authenticated as a user who can read the Base view
- Hermes must be able to send to Feishu through `hermes send --to feishu`

Optional env vars:
- `FORCE_DATE=YYYY-MM-DD` to test a historical date
- `DRY_RUN=1` to skip live sending
- `HERMES_AUTUMN_JOBS_OUTPUT_DIR=/custom/path` to override the output artifact directory
- `AUTUMN_JOBS_BASE_TOKEN`, `AUTUMN_JOBS_TABLE_ID`, `AUTUMN_JOBS_VIEW_ID` if a clone of the workflow needs different coordinates

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
FORCE_DATE=2026-07-11 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
```

Use this to prove the positive branch really sends a company list.

### Dry-run without sending Feishu message

```bash
FORCE_DATE=2026-07-11 DRY_RUN=1 python3 ~/.hermes/scripts/feishu_base_autumn_jobs_notify.py
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
Run with a known matching date such as `2026-07-11`.

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
- `message`
- `send_result`

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
2. run `lark-cli auth status` and confirm the Feishu user identity is valid
3. run the script manually with `DRY_RUN=1` to separate logic from delivery
4. run the script without `DRY_RUN` to test live delivery
5. inspect the latest result JSON artifact
6. if the schedule is suspect, list cron jobs and inspect the `feishu-base-autumn-jobs-daily` job

## Verification Checklist

- [ ] `lark-cli auth status` shows a valid user identity
- [ ] `hermes status --all` shows Feishu configured
- [ ] Manual real-send run succeeds
- [ ] Historical positive-branch test succeeds
- [ ] `latest_result.json` contains the expected fields
- [ ] Cron job exists and points to `feishu_base_autumn_jobs_notify.py`
