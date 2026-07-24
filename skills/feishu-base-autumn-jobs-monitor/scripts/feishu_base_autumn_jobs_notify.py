#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

URL = "https://campus.sma-wiki.cn/campus/campus_recruit.html?channel=zpdt"
NOWCODER_URL = "https://www.nowcoder.com/jobs/recommend/campus"
MAX_RETRIES = 2
RETRY_BASE_DELAY_SEC = 5
RETRY_BACKOFF_FACTOR = 2
TIMEZONE_NAME = "Asia/Shanghai"
OUTPUT_DIR = Path(
    os.getenv(
        "HERMES_AUTUMN_JOBS_OUTPUT_DIR",
        str(Path.home() / ".hermes/outputs/feishu-base-autumn-jobs"),
    )
).expanduser()


def _fetch_html(url: str) -> str:
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


def _resolve_url_from_nowcoder() -> str | None:
    print(f"Fetching {NOWCODER_URL} to resolve target URL ...", file=sys.stderr)
    try:
        html = _fetch_html(NOWCODER_URL)
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
    activitys = state.get("app", {}).get("108", {}).get("recommandCompany", {}).get("activitys", [])
    for entry in activitys:
        url = entry.get("url", "")
        if not url:
            continue
        if (
            "campus.sma-wiki.cn" in url
            or "网申信息" in entry.get("companyName", "")
            or "校招信息网申列表" in entry.get("name", "")
        ):
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            inner_url = unquote(qs.get("url", [""])[0])
            if inner_url:
                print(f"  resolved: {inner_url}", file=sys.stderr)
                return inner_url
    print("  no matching entry found in nowcoder activity list", file=sys.stderr)
    return None


class FetchError(Exception):
    def __init__(self, message: str, errors: list[dict[str, str]]):
        super().__init__(message)
        self.errors = errors


def _fetch_with_retry(url: str) -> str:
    errors: list[dict[str, str]] = []
    for attempt in range(MAX_RETRIES + 1):
        tag = f"[attempt {attempt + 1}/{MAX_RETRIES + 1}]"
        try:
            print(f"Fetching {url} {tag} ...", file=sys.stderr)
            html = _fetch_html(url)
            if attempt > 0:
                print(f"  Succeeded on retry {attempt}", file=sys.stderr)
            return html
        except Exception as exc:
            err = {"type": type(exc).__name__, "message": str(exc)}
            errors.append(err)
            print(f"  FAILED {tag}: {err['type']}: {err['message']}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY_SEC * (RETRY_BACKOFF_FACTOR ** attempt)
                print(f"  等待 {delay}s 后重试...", file=sys.stderr)
                time.sleep(delay)
    detail = "\n".join(f"  [{i}] {e['type']}: {e['message']}" for i, e in enumerate(errors, 1))
    raise FetchError(
        f"页面抓取失败，已重试 {MAX_RETRIES} 次（共 {MAX_RETRIES + 1} 次尝试）：\n{detail}",
        errors,
    )


def _extract_raw_data(html: str) -> list[dict[str, Any]]:
    m = re.search(r"const\s+RAW_DATA\s*=\s*(\[[\s\S]*?\])\s*;", html)
    if not m:
        raise ValueError("RAW_DATA not found in HTML")
    return json.loads(m.group(1))


def _fetch_campus_data() -> list[dict[str, Any]]:
    """Fetch campus data with retry and nowcoder failover."""
    try:
        html = _fetch_with_retry(URL)
    except FetchError as exc:
        print(str(exc), file=sys.stderr)
        resolved = _resolve_url_from_nowcoder()
        if resolved and resolved != URL:
            print(f"\n尝试从牛客网解析到新链接，进行测试...", file=sys.stderr)
            try:
                html = _fetch_with_retry(resolved)
                print(f"新链接可用: {resolved}", file=sys.stderr)
            except FetchError:
                raise RuntimeError(
                    f"所有数据源均不可用。已尝试: {URL} 和牛客网解析的 {resolved}"
                ) from None
        else:
            raise RuntimeError(f"页面抓取失败且牛客网未找到新链接") from None
    return _extract_raw_data(html)


def _require_command(name: str):
    if shutil.which(name):
        return
    raise RuntimeError(f"required command not found in PATH: {name}")


def _today_iso() -> str:
    forced = os.getenv("FORCE_DATE", "").strip()
    if forced:
        return forced
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).date().isoformat()


def _collect_today_companies(records: list[dict[str, Any]], target_date: str):
    companies: list[str] = []
    scanned_rows = 0
    first_row_date: str | None = None
    for row in records:
        scanned_rows += 1
        company = row.get("company", "")
        row_date = row.get("fullDate", "")
        if first_row_date is None:
            first_row_date = row_date
        if row_date != target_date:
            return companies, scanned_rows, first_row_date
        if company:
            companies.append(str(company).strip())
    return companies, scanned_rows, first_row_date


def _build_message(target_date: str, companies: list[str]) -> str:
    header = f"秋招监控\n日期：{target_date}\n时区：{TIMEZONE_NAME}"
    if companies:
        body = "以下公司是当日开始秋招：\n" + "\n".join(f"- {name}" for name in companies)
    else:
        body = "当日没有新增秋招的公司。"
    return header + "\n" + body


def _send_message(message: str) -> dict[str, Any]:
    if os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
        return {
            "success": True,
            "dry_run": True,
            "platform": "feishu",
            "note": "send skipped by DRY_RUN",
        }
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
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": f"invalid JSON from hermes: {proc.stdout[:200]}"}


def _now_display() -> str:
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d %H:%M:%S %Z")


def main():
    _require_command("hermes")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_date = _today_iso()
    records = _fetch_campus_data()
    companies, scanned_rows, first_row_date = _collect_today_companies(records, target_date)
    message = _build_message(target_date, companies)
    send_result = _send_message(message)
    result = {
        "ok": True,
        "target_date": target_date,
        "timezone": TIMEZONE_NAME,
        "run_at": _now_display(),
        "first_row_date": first_row_date,
        "companies": companies,
        "company_count": len(companies),
        "scanned_rows": scanned_rows,
        "message": message,
        "send_result": send_result,
        "source_url": URL,
    }
    (OUTPUT_DIR / "latest_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
