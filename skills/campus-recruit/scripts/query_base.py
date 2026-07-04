#!/usr/bin/env python3
"""
Query a Feishu Base view with time-window and field filters.

Defaults:
    - Base token: QupsbMixhaDKiqsc1CTcjJlGnGe
    - Table name: 26届秋招&春招汇总
    - View name: 先看这个表
    - Date field: 开始时间
    - Time window: last 10 days, including today
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_TOKEN = "QupsbMixhaDKiqsc1CTcjJlGnGe"
DEFAULT_TABLE_NAME = "26届秋招&春招汇总"
DEFAULT_VIEW_NAME = "先看这个表"
DEFAULT_DATE_FIELD = "开始时间"
DEFAULT_FIELDS = [
    "公司",
    "岗位",
    "开始时间",
    "截止日期",
    "工作地点",
    "学历要求",
    "招聘类型",
    "公司行业",
    "是否免笔试",
    "公告链接",
    "投递链接",
]


@dataclass
class FieldFilter:
    field: str
    values: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query a Feishu Base view with lark-cli")
    parser.add_argument("--base-token", default=DEFAULT_BASE_TOKEN)
    parser.add_argument("--table-id", default="", help="Table ID or table name override")
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    parser.add_argument("--view-id", default="", help="View ID or view name override")
    parser.add_argument("--view-name", default=DEFAULT_VIEW_NAME)
    parser.add_argument("--date-field", default=DEFAULT_DATE_FIELD)
    parser.add_argument("--days", type=int, default=10, help="Last N days, including today")
    parser.add_argument("--field", action="append", default=[], help="Returned field, repeatable")
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        help="Structured filter in the form Field=value1,value2",
    )
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--identity", choices=["user", "bot"], default="")
    parser.add_argument("--as-user", action="store_true", help="Shortcut for --identity user")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def ensure_lark_cli_available() -> None:
    if subprocess.run(["which", "lark-cli"], capture_output=True, text=True, check=False).returncode != 0:
        raise RuntimeError("lark-cli not found in PATH")


def parse_where_clause(raw: str) -> FieldFilter:
    if "=" not in raw:
        raise ValueError(f"Invalid filter: {raw}. Expected Field=value1,value2")
    field, raw_values = raw.split("=", 1)
    field = field.strip()
    values = [value.strip() for value in raw_values.split(",") if value.strip()]
    if not field or not values:
        raise ValueError(f"Invalid filter: {raw}. Field and values must be non-empty")
    return FieldFilter(field=field, values=values)


def build_filter_json(date_field: str, days: int, raw_filters: list[str]) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("--days must be greater than 0")

    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days - 1)
    date_cutoff = start_date - dt.timedelta(days=1)

    conditions: list[list[Any]] = [
        [date_field, ">", f"ExactDate({date_cutoff.isoformat()})"],
    ]

    for raw_filter in raw_filters:
        parsed = parse_where_clause(raw_filter)
        operator = "==" if len(parsed.values) == 1 else "intersects"
        value: str | list[str] = parsed.values[0] if len(parsed.values) == 1 else parsed.values
        conditions.append([parsed.field, operator, value])

    return {"logic": "and", "conditions": conditions}


def build_sort_json(date_field: str) -> list[dict[str, Any]]:
    return [{"field": date_field, "desc": True}]


def extract_lark_error_message(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text.strip() or "Unknown command error"

    error = payload.get("error")
    if not isinstance(error, dict):
        return raw_text.strip() or "Unknown command error"

    parts = []
    message = error.get("message")
    hint = error.get("hint")
    console_url = error.get("console_url")
    if message:
        parts.append(str(message))
    if hint:
        parts.append(f"hint: {hint}")
    if console_url:
        parts.append(f"console_url: {console_url}")
    if parts:
        return "\n".join(parts)
    return raw_text.strip() or "Unknown command error"


def build_lark_auth_hint(raw_text: str) -> str:
    lower_text = raw_text.lower()

    if "keychain get failed" in lower_text or "keychain not initialized" in lower_text:
        return (
            "Authentication fallback:\n"
            "- Re-run in an environment that can access the macOS keychain\n"
            "- Or run `lark-cli config init`\n"
            "- If this is a sandbox or automation session on macOS, run "
            "`lark-cli config keychain-downgrade` once in an interactive terminal"
        )

    if "auth login" in lower_text or "device_code" in lower_text:
        return (
            "Authentication fallback:\n"
            "- Authorize the user identity with `lark-cli auth login --scope \"<missing_scope>\"`\n"
            "- Or use `lark-cli auth login --domain <domain>` when the API domain is known"
        )

    if "permission_violations" in lower_text or "missing_scope" in lower_text or "console_url" in lower_text:
        return (
            "Authentication fallback:\n"
            "- If you are using `--as user`, run `lark-cli auth login --scope \"<missing_scope>\"`\n"
            "- If you are using `--as bot`, open the returned `console_url` and enable the missing scope"
        )

    if "\"type\": \"config\"" in raw_text or "\"type\":\"config\"" in raw_text:
        return (
            "Authentication fallback:\n"
            "- Check whether `lark-cli` has been configured for this machine\n"
            "- Run `lark-cli config init` if configuration is missing"
        )

    return ""


def format_lark_command_error(completed: subprocess.CompletedProcess[str]) -> str:
    raw_text = completed.stderr.strip() or completed.stdout.strip() or "Unknown command error"
    message = extract_lark_error_message(raw_text)
    auth_hint = build_lark_auth_hint(raw_text)
    if auth_hint:
        return f"{message}\n\n{auth_hint}"
    return message


def run_json_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(format_lark_command_error(completed))
    return json.loads(completed.stdout)


def identity_args(args: argparse.Namespace) -> list[str]:
    identity = "user" if args.as_user else args.identity
    if identity:
        return ["--as", identity]
    return []


def build_record_list_command(
    args: argparse.Namespace,
    fields: list[str],
    filter_json: dict[str, Any],
) -> list[str]:
    table_ref = args.table_id or args.table_name
    view_ref = args.view_id or args.view_name
    command = [
        "lark-cli",
        "base",
        "+record-list",
        "--base-token",
        args.base_token,
        "--table-id",
        table_ref,
        "--view-id",
        view_ref,
        "--limit",
        str(args.limit),
        "--offset",
        str(args.offset),
        "--format",
        "json",
        "--sort-json",
        json.dumps(build_sort_json(args.date_field), ensure_ascii=False),
        "--filter-json",
        json.dumps(filter_json, ensure_ascii=False),
        *identity_args(args),
    ]
    for field in fields:
        command.extend(["--field-id", field])
    return command


def main() -> int:
    try:
        args = parse_args()
        ensure_lark_cli_available()
        fields = args.field or DEFAULT_FIELDS
        filter_json = build_filter_json(args.date_field, args.days, args.where)
        command = build_record_list_command(args, fields, filter_json)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps({
            "base_token": args.base_token,
            "table_ref": args.table_id or args.table_name,
            "table_name": args.table_name,
            "view_ref": args.view_id or args.view_name,
            "view_name": args.view_name,
            "command": command,
            "filter_json": filter_json,
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        result = run_json_command(command)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "base_token": args.base_token,
        "table_ref": args.table_id or args.table_name,
        "table_name": args.table_name,
        "view_ref": args.view_id or args.view_name,
        "view_name": args.view_name,
        "date_range": {
            "days": args.days,
            "date_field": args.date_field,
        },
        "fields": fields,
        "where": args.where,
        "result": result,
    }
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
