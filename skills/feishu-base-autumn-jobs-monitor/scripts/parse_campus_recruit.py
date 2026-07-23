#!/usr/bin/env python3
"""
Parse the 27届校招 page from campus.sma-wiki.cn and extract all job entries.

Usage:
    python3 scripts/parse_campus_recruit.py                    # fetch live page (retry up to 3x, notify Feishu on failure)
    python3 scripts/parse_campus_recruit.py --file cached.html # parse local file
    python3 scripts/parse_campus_recruit.py --json             # output JSON
    python3 scripts/parse_campus_recruit.py --no-notify        # fetch but do not send Feishu notification on failure

Output (default): summary table + field inventory.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

URL = "https://campus.sma-wiki.cn/campus/campus_recruit.html?channel=zpdt"
NOWCODER_URL = "https://www.nowcoder.com/jobs/recommend/campus"
MAX_RETRIES = 2
RETRY_BASE_DELAY_SEC = 5
RETRY_BACKOFF_FACTOR = 2
TIMEZONE_NAME = "Asia/Shanghai"

FIELD_MAP_CN = {
    "updateDate": "更新日期",
    "fullDate": "完整日期",
    "month": "月份",
    "company": "公司名称",
    "batch": "批次",
    "location": "工作地点",
    "positions": "招聘岗位",
    "sourceLink": "信息源链接",
    "linkSource": "信息来源标签",
    "appLink": "网申链接",
    "isKey": "是否重点公司",
    "evaluation": "公司简介",
    "industry": "行业",
    "nature": "公司性质",
    "deadline": "截止日期",
    "isCommercial": "是否商业推广",
    "commercialExpiry": "商业推广过期",
    "isFeaturedCard": "是否精选卡片",
}


def _now_display() -> str:
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d %H:%M:%S %Z")


def _run_hermes_send(message: str) -> dict[str, Any]:
    if os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
        return {"success": True, "dry_run": True, "platform": "feishu", "note": "send skipped by DRY_RUN"}
    if not shutil.which("hermes"):
        return {"success": False, "error": "hermes command not found in PATH"}
    proc = subprocess.run(
        ["hermes", "send", "--to", "feishu", "--file", "-", "--json"],
        input=message,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"success": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    try:
        result = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": f"invalid JSON from hermes: {proc.stdout[:200]}"}
    return result


def send_failure_notification(errors: list[dict[str, str]], url: str) -> None:
    """Send a Feishu notification detailing all fetch failures."""
    ts = _now_display()
    header = f"校招页面抓取失败通知\n时间：{ts}\n目标页面：{url}"
    body_lines = [f"\n重试 {len(errors)} 次均失败："]
    for i, err in enumerate(errors, 1):
        body_lines.append(f"\n第 {i} 次：")
        body_lines.append(f"  错误类型：{err['type']}")
        body_lines.append(f"  错误信息：{err['message']}")
    message = header + "\n".join(body_lines)
    try:
        result = _run_hermes_send(message)
        if result.get("success"):
            print(f"[{_now_display()}] 已通过飞书通报访问失败", file=sys.stderr)
        else:
            print(f"[{_now_display()}] 飞书通报失败: {result.get('error', 'unknown')}", file=sys.stderr)
    except Exception as exc:
        print(f"[{_now_display()}] 飞书通报异常: {exc}", file=sys.stderr)


def fetch_html(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    encoding = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(encoding, errors="replace")


def resolve_url_from_nowcoder() -> str | None:
    """
    Fetch the nowcoder jobs page and extract the target campus_recruit URL
    from the __INITIAL_STATE__ JSON. Returns the URL or None on any failure.
    """
    import urllib.parse

    print(f"Fetching {NOWCODER_URL} to resolve target URL ...", file=sys.stderr)
    try:
        html = fetch_html(NOWCODER_URL)
    except Exception as exc:
        print(f"  nowcoder fetch failed: {exc}", file=sys.stderr)
        return None

    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});", html, re.DOTALL)
    if not m:
        print("  __INITIAL_STATE__ not found in nowcoder page", file=sys.stderr)
        return None

    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        print(f"  failed to parse __INITIAL_STATE__: {exc}", file=sys.stderr)
        return None

    activitys = (
        state.get("app", {})
        .get("108", {})
        .get("recommandCompany", {})
        .get("activitys", [])
    )

    for entry in activitys:
        url = entry.get("url", "")
        company_name = entry.get("companyName", "")
        if not url:
            continue
        is_target = (
            "campus.sma-wiki.cn" in url
            or "网申信息" in company_name
            or "校招信息网申列表" in entry.get("name", "")
        )
        if not is_target:
            continue

        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        inner_url = urllib.parse.unquote(qs.get("url", [""])[0])
        if inner_url:
            print(f"  resolved: {inner_url}", file=sys.stderr)
            return inner_url

    print("  no matching entry found in nowcoder activity list", file=sys.stderr)
    return None


class FetchError(Exception):
    """Raised when all fetch retries are exhausted. Carries detailed error list for notification."""

    def __init__(self, message: str, errors: list[dict[str, str]]):
        super().__init__(message)
        self.errors = errors


def fetch_with_retry(url: str) -> str:
    """Fetch URL with retries and exponential backoff, raising FetchError on exhaustion."""
    errors: list[dict[str, str]] = []
    for attempt in range(MAX_RETRIES + 1):
        tag = f"[attempt {attempt + 1}/{MAX_RETRIES + 1}]"
        try:
            print(f"Fetching {url} {tag} ...", file=sys.stderr)
            html = fetch_html(url)
            if attempt > 0:
                print(f"  Succeeded on retry {attempt}", file=sys.stderr)
            return html
        except Exception as exc:
            err = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            errors.append(err)
            print(f"  FAILED {tag}: {err['type']}: {err['message']}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY_SEC * (RETRY_BACKOFF_FACTOR ** attempt)
                print(f"  等待 {delay}s 后重试...", file=sys.stderr)
                time.sleep(delay)

    detail = "\n".join(
        f"  [{i}] {e['type']}: {e['message']}" for i, e in enumerate(errors, 1)
    )
    raise FetchError(
        f"页面抓取失败，已重试 {MAX_RETRIES} 次（共 {MAX_RETRIES + 1} 次尝试）：\n{detail}",
        errors,
    )


def extract_raw_data(html: str) -> list[dict[str, Any]]:
    m = re.search(r"const\s+RAW_DATA\s*=\s*(\[[\s\S]*?\])\s*;", html)
    if not m:
        raise ValueError("RAW_DATA not found in HTML")
    return json.loads(m.group(1))


def field_inventory(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    inv: dict[str, dict[str, Any]] = {}
    for r in records:
        for k, v in r.items():
            if k not in inv:
                inv[k] = {
                    "cn_name": FIELD_MAP_CN.get(k, k),
                    "types": Counter(),
                    "null_count": 0,
                    "sample_values": [],
                }
            if v is None or v == "":
                inv[k]["null_count"] += 1
            else:
                inv[k]["types"][type(v).__name__] += 1
                if len(inv[k]["sample_values"]) < 3:
                    inv[k]["sample_values"].append(str(v)[:80])
    return inv


def print_inventory(records: list[dict[str, Any]]) -> None:
    inv = field_inventory(records)

    print(f"\n{'='*70}")
    print(f"  27届校招页面字段识别")
    print(f"  记录总数: {len(records)}")
    print(f"{'='*70}\n")

    print(f"{'字段名':<22} {'中文名':<16} {'类型':<14} {'非空率':<10} {'示例'}")
    print("-" * 100)

    for field, meta in inv.items():
        total = len(records)
        non_empty = total - meta["null_count"]
        ratio = f"{non_empty}/{total}" if non_empty < total else f"{total}/{total}"
        types_str = "+".join(f"{t}({c})" for t, c in meta["types"].most_common(2))
        sample = meta["sample_values"][0] if meta["sample_values"] else "(空)"
        print(
            f"  {field:<20} "
            f"{meta['cn_name']:<14} "
            f"{types_str:<14} "
            f"{ratio:<10} "
            f"{sample}"
        )

    print("\n" + "-" * 100)
    print("  所有字段识别完毕。")


def print_stats(records: list[dict[str, Any]]) -> None:
    batches = Counter(r.get("batch", "") for r in records)
    industries = Counter(r.get("industry", "") for r in records)
    natures = Counter(r.get("nature", "") for r in records)
    key_count = sum(1 for r in records if r.get("isKey"))
    featured_count = sum(1 for r in records if r.get("isFeaturedCard"))
    latest_date = max((r.get("fullDate", "") for r in records), default="")

    print(f"\n{'='*70}")
    print(f"  统计概览")
    print(f"{'='*70}")
    print(f"  最新更新日期: {latest_date}")
    print(f"  重点公司数:   {key_count}")
    print(f"  精选卡片数:   {featured_count}")
    print(f"\n  批次分布:")
    for k, c in batches.most_common():
        print(f"    {k:<20} {c:>5}")
    print(f"\n  行业分布 (Top 10):")
    for k, c in industries.most_common(10):
        print(f"    {k:<20} {c:>5}")
    print(f"\n  公司性质分布:")
    for k, c in natures.most_common():
        print(f"    {k:<20} {c:>5}")


def smoke_test(records: list[dict[str, Any]]) -> bool:
    """Run smoke test assertions against parsed data."""
    errors: list[str] = []

    # Expected fields
    expected_fields = set(FIELD_MAP_CN.keys())
    actual_fields = set(records[0].keys()) if records else set()
    missing = expected_fields - actual_fields
    extra = actual_fields - expected_fields
    if missing:
        errors.append(f"缺少字段: {missing}")
    if extra:
        errors.append(f"额外字段: {extra}")

    # Minimum record count
    if len(records) < 100:
        errors.append(f"记录数过少: {len(records)} (期望 >= 100)")

    # All records should have same fields
    field_sets = [set(r.keys()) for r in records]
    if len(set(frozenset(fs) for fs in field_sets)) != 1:
        errors.append("部分记录字段不一致")

    # No empty company names
    empty_companies = sum(1 for r in records if not r.get("company"))
    if empty_companies > 0:
        errors.append(f"{empty_companies} 条记录的公司名称为空")

    # fullDate format check
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    bad_dates = sum(1 for r in records if r.get("fullDate") and not date_pattern.match(r["fullDate"]))
    if bad_dates > 0:
        errors.append(f"{bad_dates} 条记录的 fullDate 格式不正确")

    print(f"\n{'='*70}")
    print(f"  冒烟测试结果")
    print(f"{'='*70}")
    if errors:
        print(f"  FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"    ✗ {e}")
    else:
        print(f"  PASSED - {len(records)} 条记录解析正常")
    print()
    return len(errors) == 0


def main() -> None:
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}

    if "--help" in flags or "-h" in flags:
        print(__doc__)
        return

    if "--file" in flags:
        idx = args.index("--file")
        try:
            path = args[idx + 1]
        except IndexError:
            print("Error: --file requires a path", file=sys.stderr)
            sys.exit(2)
        with open(path, encoding="utf-8") as f:
            html = f.read()
    else:
        target_url = URL
        try:
            html = fetch_with_retry(target_url)
        except FetchError as exc:
            print(str(exc), file=sys.stderr)

            # Failover: try to resolve a fresh URL from nowcoder
            resolved = resolve_url_from_nowcoder()
            if resolved and resolved != target_url:
                print(f"\n尝试从牛客网解析到新链接，进行测试...", file=sys.stderr)
                try:
                    html = fetch_with_retry(resolved)
                    print(f"新链接可用: {resolved}", file=sys.stderr)
                except FetchError as exc2:
                    print(f"新链接同样不可用: {exc2}", file=sys.stderr)
                    if "--no-notify" not in flags:
                        all_errors = exc.errors + [
                            {"type": "Info", "message": f"已尝试从牛客网解析新链接: {resolved}"}
                        ] + exc2.errors
                        send_failure_notification(all_errors, target_url)
                    sys.exit(3)
            else:
                if "--no-notify" not in flags:
                    send_failure_notification(exc.errors, target_url)
                sys.exit(3)
        except Exception as exc:
            print(f"Unexpected error during fetch: {type(exc).__name__}: {exc}", file=sys.stderr)
            if "--no-notify" not in flags:
                send_failure_notification(
                    [{"type": type(exc).__name__, "message": str(exc)}],
                    target_url,
                )
            sys.exit(3)

    try:
        records = extract_raw_data(html)
    except Exception as exc:
        print(f"数据解析失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "--no-notify" not in flags and "--file" not in flags:
            send_failure_notification(
                [{"type": type(exc).__name__, "message": str(exc)}],
                URL,
            )
        sys.exit(4)

    if "--json" in flags:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    print_inventory(records)
    print_stats(records)

    ok = smoke_test(records)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
