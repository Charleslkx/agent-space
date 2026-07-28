#!/usr/bin/env python3
import argparse
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
NON_INTERNSHIP_BATCH_EXCLUSION = "实习"
DEFAULT_SOURCE_URL = URL


def _sanitize_url_candidate(url: str) -> str:
    cleaned = url.strip().replace("\\_", "_")
    cleaned = cleaned.split("](", 1)[0]
    cleaned = cleaned.split("<", 1)[0]
    cleaned = cleaned.split(">", 1)[0]
    return cleaned.rstrip(",.;'\")")


def _unwrap_redirect_url(url: str) -> str:
    current = _sanitize_url_candidate(url)
    for _ in range(3):
        parsed = urlparse(current)
        qs = parse_qs(parsed.query)
        wrapped = None
        for key in ("target", "target_url", "url"):
            values = qs.get(key, [])
            if values and values[0].startswith(("http://", "https://")):
                wrapped = _sanitize_url_candidate(unquote(values[0]))
                break
        if not wrapped or wrapped == current:
            return current
        current = _sanitize_url_candidate(wrapped)
    return current


def _extract_urls(value: Any) -> list[str]:
    if not value or not isinstance(value, str):
        return []
    s = value.strip()
    if not s:
        return []

    candidates: list[str] = []
    parts = re.split(r"\]\(|<|>|\s+", s)
    for part in parts:
        for match in re.finditer(r"https?://[^\s]+", part):
            raw = match.group(0)
            cleaned = _sanitize_url_candidate(raw)
            if cleaned.startswith(("http://", "https://")):
                unwrapped = _unwrap_redirect_url(cleaned)
                if unwrapped not in candidates:
                    candidates.append(unwrapped)
    return candidates


def _score_url(url: str) -> tuple[int, int, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    score = 0
    if host == "link.wtturl.cn":
        score -= 5
    if host == "mp.weixin.qq.com" and path.startswith("/mp/wappoc_appmsgcaptcha"):
        score -= 3
    if host == "mp.weixin.qq.com" and path.startswith("/s"):
        score += 3
    if host.endswith("sma-wiki.cn"):
        score += 2
    if host:
        score += 1
    return (score, -len(url), url)


def _extract_url(value: Any) -> str | None:
    candidates = _extract_urls(value)
    if not candidates:
        return None
    return max(candidates, key=_score_url)


def _company_markdown(company: str, app_link: Any, source_link: Any) -> str:
    url = _extract_url(app_link) or _extract_url(source_link)
    if url:
        return f"[{company}]({url})"
    return company


def _page_source_markdown(source_url: str) -> str:
    label = "sma-wiki校招页"
    if source_url != DEFAULT_SOURCE_URL:
        label = "当前数据源"
    return f"[{label}]({source_url})"


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


def _fetch_campus_data() -> tuple[list[dict[str, Any]], str]:
    """Fetch campus data with retry and nowcoder failover."""
    source_url = URL
    try:
        html = _fetch_with_retry(URL)
    except FetchError as exc:
        print(str(exc), file=sys.stderr)
        resolved = _resolve_url_from_nowcoder()
        if resolved and resolved != URL:
            print(f"\n尝试从牛客网解析到新链接，进行测试...", file=sys.stderr)
            try:
                html = _fetch_with_retry(resolved)
                source_url = resolved
                print(f"新链接可用: {resolved}", file=sys.stderr)
            except FetchError:
                raise RuntimeError(
                    f"所有数据源均不可用。已尝试: {URL} 和牛客网解析的 {resolved}"
                ) from None
        else:
            raise RuntimeError(f"页面抓取失败且牛客网未找到新链接") from None
    return _extract_raw_data(html), source_url


def _require_command(name: str):
    if shutil.which(name):
        return
    raise RuntimeError(f"required command not found in PATH: {name}")


def _normalize_date_input(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("empty date")

    normalized = (
        raw.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    formats = ["%Y-%m-%d", "%Y%m%d", "%m-%d", "%m%d"]
    current_year = datetime.now(ZoneInfo(TIMEZONE_NAME)).year
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized, fmt)
            if fmt in {"%m-%d", "%m%d"}:
                parsed = parsed.replace(year=current_year)
            return parsed.date().isoformat()
        except ValueError:
            continue
    raise ValueError(
        f"invalid date '{value}'; supported examples: 2026-07-25, 2026/7/25, 20260725, 7.25, 7月25日"
    )


def _validate_date_arg(value: str) -> str:
    try:
        return _normalize_date_input(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="查询指定日期的秋招信息并发送/预览通知"
    )
    parser.add_argument(
        "date",
        nargs="?",
        type=_validate_date_arg,
        help="要查询的日期；支持 2026-07-25、2026/7/25、20260725、7.25、7月25日；省略时默认为 Asia/Shanghai 今天",
    )
    return parser.parse_args()


def _resolve_target_date(cli_date: str | None) -> str:
    if cli_date:
        return cli_date
    forced = os.getenv("FORCE_DATE", "").strip()
    if forced:
        return _normalize_date_input(forced)
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).date().isoformat()


def _should_include_row(row: dict[str, Any], target_date: str) -> bool:
    if row.get("fullDate", "") != target_date:
        return False
    batch = str(row.get("batch", "")).strip()
    if NON_INTERNSHIP_BATCH_EXCLUSION in batch:
        return False
    return True


def _collect_target_companies(records: list[dict[str, Any]], target_date: str):
    companies: list[dict[str, str | None]] = []
    scanned_rows = len(records)
    first_row_date: str | None = records[0].get("fullDate", "") if records else None
    for row in records:
        if not _should_include_row(row, target_date):
            continue
        company = row.get("company", "")
        if company:
            company_name = str(company).strip()
            app_link = row.get("appLink")
            source_link = row.get("sourceLink")
            batch = str(row.get("batch", "")).strip() or None
            companies.append(
                {
                    "company": company_name,
                    "company_markdown": _company_markdown(company_name, app_link, source_link),
                    "app_link": _extract_url(app_link),
                    "source_link": _extract_url(source_link),
                    "batch": batch,
                }
            )
    return companies, scanned_rows, first_row_date


def _build_message(
    target_date: str,
    companies: list[dict[str, str | None]],
    source_url: str,
) -> str:
    header = (
        f"秋招监控\n"
        f"数据源：{_page_source_markdown(source_url)}\n"
        f"日期：{target_date}\n"
        f"时区：{TIMEZONE_NAME}"
    )
    if companies:
        body = "以下公司是当日开始秋招：\n" + "\n".join(
            f"- {item['company_markdown']}"
            + (f"｜批次：{item['batch']}" if item.get('batch') else "")
            for item in companies
        )
    else:
        body = f"当日没有新增秋招的公司（已过滤批次中包含“{NON_INTERNSHIP_BATCH_EXCLUSION}”的记录）。"
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
    args = _parse_args()
    _require_command("hermes")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_date = _resolve_target_date(args.date)
    records, source_url = _fetch_campus_data()
    companies, scanned_rows, first_row_date = _collect_target_companies(records, target_date)
    message = _build_message(target_date, companies, source_url)
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
        "source_url": source_url,
    }
    (OUTPUT_DIR / "latest_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
