#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_TOKEN = os.getenv("AUTUMN_JOBS_BASE_TOKEN", "QupsbMixhaDKiqsc1CTcjJlGnGe")
TABLE_ID = os.getenv("AUTUMN_JOBS_TABLE_ID", "tblyww7RWoFyBq2I")
VIEW_ID = os.getenv("AUTUMN_JOBS_VIEW_ID", "vewlapetfU")
FIELDS = ["公司", "开始时间"]
LIMIT = 200
TIMEZONE_NAME = "Asia/Shanghai"
OUTPUT_DIR = Path(os.getenv("HERMES_AUTUMN_JOBS_OUTPUT_DIR", str(Path.home() / ".hermes/outputs/feishu-base-autumn-jobs"))).expanduser()


def run(cmd, *, input_text=None):
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def require_command(name: str):
    if shutil.which(name):
        return
    raise RuntimeError(f"required command not found in PATH: {name}")


def parse_date(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def today_iso() -> str:
    forced = os.getenv("FORCE_DATE", "").strip()
    if forced:
        return forced
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).date().isoformat()


def fetch_page(offset: int):
    cmd = [
        "lark-cli", "base", "+record-list",
        "--base-token", BASE_TOKEN,
        "--table-id", TABLE_ID,
        "--view-id", VIEW_ID,
        "--limit", str(LIMIT),
        "--offset", str(offset),
        "--format", "json",
        "--as", "user",
    ]
    for field in FIELDS:
        cmd.extend(["--field-id", field])
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"record-list failed: {proc.stderr.strip() or proc.stdout.strip()}")
    payload = json.loads(proc.stdout)
    if not payload.get("ok"):
        raise RuntimeError(f"record-list returned not ok: {payload}")
    return payload["data"]


def collect_today_companies(target_date: str):
    companies = []
    scanned_rows = 0
    offset = 0
    first_row_date = None
    while True:
        data = fetch_page(offset)
        rows = data.get("data", [])
        if not rows:
            break
        for row in rows:
            scanned_rows += 1
            company = row[0] if len(row) > 0 else None
            start_time = row[1] if len(row) > 1 else None
            row_date = parse_date(start_time) if isinstance(start_time, str) else None
            if first_row_date is None:
                first_row_date = row_date
            if row_date != target_date:
                return companies, scanned_rows, first_row_date
            if company:
                companies.append(str(company).strip())
        if not data.get("has_more"):
            break
        offset += len(rows)
    return companies, scanned_rows, first_row_date


def build_message(target_date: str, companies: list[str]) -> str:
    header = f"秋招监控\n日期：{target_date}\n时区：{TIMEZONE_NAME}"
    if companies:
        body = "以下公司是当日开始秋招：\n" + "\n".join(f"- {name}" for name in companies)
    else:
        body = "当日没有新增秋招的公司。"
    return header + "\n" + body


def send_message(message: str):
    if os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
        return {"success": True, "dry_run": True, "platform": "feishu", "note": "send skipped by DRY_RUN"}
    proc = run(["hermes", "send", "--to", "feishu", "--file", "-", "--json"], input_text=message)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes send failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)


def now_display() -> str:
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d %H:%M:%S %Z")


def main():
    require_command("lark-cli")
    require_command("hermes")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_date = today_iso()
    companies, scanned_rows, first_row_date = collect_today_companies(target_date)
    message = build_message(target_date, companies)
    send_result = send_message(message)
    result = {
        "ok": True,
        "target_date": target_date,
        "timezone": TIMEZONE_NAME,
        "run_at": now_display(),
        "first_row_date": first_row_date,
        "companies": companies,
        "company_count": len(companies),
        "scanned_rows": scanned_rows,
        "message": message,
        "send_result": send_result,
        "base_token": BASE_TOKEN,
        "table_id": TABLE_ID,
        "view_id": VIEW_ID,
    }
    (OUTPUT_DIR / "latest_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
