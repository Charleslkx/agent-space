#!/usr/bin/env python3
"""
Parse campus recruitment records from a Feishu Base and output
by industry-grouped format.  Wraps query_base.py with a simpler
CLI:  --today / -n / -o text|json / --group full|simplified.

Usage examples (from project root):

    # With env-check:
    ./scripts/run_with_env_check.sh python3 scripts/parse_campus_recruit.py --today -n 100 -o text

    # Direct uv (must inject PATH for lark-cli):
    UV_CACHE_DIR=.uv-cache PATH="$HOME/.npm-global/bin:$PATH" uv run python3 scripts/parse_campus_recruit.py --days 30 -n 200 -o json

    # Simplified 4-category mode (金融/互联网/国央企/其他):
    UV_CACHE_DIR=.uv-cache PATH="$HOME/.npm-global/bin:$PATH" uv run python3 scripts/parse_campus_recruit.py --days 10 --group simplified -n 200 -o text

    # Filter by recruitment type:
    UV_CACHE_DIR=.uv-cache PATH="$HOME/.npm-global/bin:$PATH" uv run python3 scripts/parse_campus_recruit.py --days 10 --group simplified --where "招聘类型=秋招,秋招提前批,暑期实习,实习" -n 200 -o text
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Industry classification — full mode (9 categories)
# ---------------------------------------------------------------------------

TAG_TO_CATEGORY = {
    "互联网": "互联网/科技",
    "游戏": "互联网/科技",
    "软件": "互联网/科技",
    "通信": "互联网/科技",
    "人工智能": "互联网/科技",
    "AI": "互联网/科技",
    "金融": "金融/银行",
    "银行": "金融/银行",
    "基金": "金融/银行",
    "证券": "金融/银行",
    "保险": "金融/银行",
    "消费金融": "金融/银行",
    "投资": "金融/银行",
    "资管": "金融/银行",
    "消费": "消费/零售",
    "零售": "消费/零售",
    "快消零售": "消费/零售",
    "快消": "消费/零售",
    "电商": "消费/零售",
    "制造业": "制造/工业",
    "制造": "制造/工业",
    "工业": "制造/工业",
    "机械": "制造/工业",
    "半导体": "制造/工业",
    "芯片": "制造/工业",
    "电子": "制造/工业",
    "化工": "制造/工业",
    "建筑": "制造/工业",
    "基建": "制造/工业",
    "建材": "制造/工业",
    "汽车新能源": "汽车",
    "汽车": "汽车",
    "新能源": "能源/环保",
    "能源": "能源/环保",
    "电力": "能源/环保",
    "电网": "能源/环保",
    "环保": "能源/环保",
    "光伏": "能源/环保",
    "航空": "航空/航天",
    "航天": "航空/航天",
    "航空/航天": "航空/航天",
    "民航": "航空/航天",
    "教育": "教育",
}

GENERIC_TAGS = {"国央企", "外企", "中外合资", "事业单位", "央企", "国企"}
BROAD_TAGS = {"科技"}
ORDERED_CATEGORIES = [
    "互联网/科技", "金融/银行", "消费/零售", "制造/工业",
    "汽车", "能源/环保", "航空/航天", "教育", "其他",
]

# ---------------------------------------------------------------------------
# Industry classification — simplified mode (4 categories)
# ---------------------------------------------------------------------------

SIMPLIFIED_TAG_TO_CATEGORY = {
    # 金融 (priority 1)
    "金融": "金融", "银行": "金融", "基金": "金融", "证券": "金融",
    "保险": "金融", "消费金融": "金融", "投资": "金融", "资管": "金融",
    # 互联网 (priority 2)
    "互联网": "互联网", "游戏": "互联网", "软件": "互联网", "通信": "互联网",
    "人工智能": "互联网", "AI": "互联网", "科技": "互联网", "电商": "互联网",
    # 国央企 (priority 3 — ownership tags become a real category)
    "国央企": "国央企", "央企": "国央企", "国企": "国央企", "事业单位": "国央企",
}

SIMPLIFIED_ORDERED_CATEGORIES = ["金融", "互联网", "国央企", "其他"]


def classify_industry(industry_tags: list[str], group: str = "full") -> str:
    """Map raw industry tags to an output category.

    group="full": original 9-category classification.
    group="simplified": 4-category classification (金融, 互联网, 国央企, 其他).
    """
    if group == "simplified":
        return _classify_simplified(industry_tags)
    return _classify_full(industry_tags)


def _classify_full(industry_tags: list[str]) -> str:
    """Original 9-category classification."""
    if not industry_tags:
        return "其他"
    cleaned = [t.strip() for t in industry_tags if t.strip()]

    # First pass: specific (non-generic, non-broad) tags
    for tag in cleaned:
        if tag in GENERIC_TAGS or tag in BROAD_TAGS:
            continue
        if tag in TAG_TO_CATEGORY:
            return TAG_TO_CATEGORY[tag]

    # Second pass: broad tag "科技" alone (no generic tag)
    broad_only = [t for t in cleaned if t in BROAD_TAGS]
    if broad_only and not any(t in GENERIC_TAGS for t in cleaned):
        return "互联网/科技"

    # Fallback pass: any remaining match
    for tag in cleaned:
        if tag in TAG_TO_CATEGORY:
            return TAG_TO_CATEGORY[tag]
    return "其他"


def _classify_simplified(industry_tags: list[str]) -> str:
    """Simplified 4-category classification: 金融, 互联网, 国央企, 其他.

    Priority: 金融 > 互联网 > 国央企 > 其他.
    Tags not in SIMPLIFIED_TAG_TO_CATEGORY fall through to 其他.
    """
    if not industry_tags:
        return "其他"
    cleaned = [t.strip() for t in industry_tags if t.strip()]

    # Priority 1: 金融
    for tag in cleaned:
        if tag in SIMPLIFIED_TAG_TO_CATEGORY and SIMPLIFIED_TAG_TO_CATEGORY[tag] == "金融":
            return "金融"

    # Priority 2: 互联网
    for tag in cleaned:
        if tag in SIMPLIFIED_TAG_TO_CATEGORY and SIMPLIFIED_TAG_TO_CATEGORY[tag] == "互联网":
            return "互联网"

    # Priority 3: 国央企/事业单位
    for tag in cleaned:
        if tag in SIMPLIFIED_TAG_TO_CATEGORY and SIMPLIFIED_TAG_TO_CATEGORY[tag] == "国央企":
            return "国央企"

    return "其他"


def parse_record_date(start_date_val) -> date | None:
    """Extract a date from the 开始时间 field (index 2).

    The field may be a string like '2026-05-29 00:00:00', a numeric
    timestamp (seconds or milliseconds), or None.
    """
    if start_date_val is None:
        return None
    if isinstance(start_date_val, (int, float)):
        # Feishu sometimes returns Unix timestamps in milliseconds
        ts = start_date_val
        if ts > 1e12:  # milliseconds
            ts = ts / 1000
        return date.fromtimestamp(ts)
    s = str(start_date_val).strip()
    if not s:
        return None
    # Try common date formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def format_location(locations: list[str]) -> str:
    if not locations:
        return "未明确"
    joined = "/".join(locations[:3])
    return f"{joined}等" if len(locations) > 3 else joined


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def run_query(args: argparse.Namespace) -> list[list]:
    """Call `lark-cli base +record-list` via query_base.py subprocess."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    query_path = os.path.join(script_dir, "query_base.py")

    cmd = [
        sys.executable, query_path,
        "--days", str(args.days),
        "--limit", str(args.limit),
    ]
    if args.as_user:
        cmd.append("--as-user")
    if args.identity:
        cmd.extend(["--identity", args.identity])
    for field in args.field:
        cmd.extend(["--field", field])
    for w in args.where:
        cmd.extend(["--where", w])

    env = {**os.environ, "UV_CACHE_DIR": os.path.join(os.path.dirname(script_dir), ".uv-cache")}
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)

    if completed.returncode != 0:
        err = completed.stderr.strip() or completed.stdout.strip() or "query_base.py failed"
        raise RuntimeError(err)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse query_base.py output: {exc}") from exc

    records: list[list] = payload["result"]["data"]["data"]
    return records


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _build_company_groups(
    records: list[list], group: str, today: date,
) -> tuple[OrderedDict[str, dict], OrderedDict[str, dict]]:
    """Split records into today-only and recent-but-not-today groups.

    Returns (today_companies, recent_companies) where each is an
    OrderedDict keyed by company name with values:
        {"cat": str, "batch": set, "loc": str}
    Same company may appear in both groups if it has records on different dates.
    """
    today_data: OrderedDict[str, dict] = OrderedDict()
    recent_data: OrderedDict[str, dict] = OrderedDict()

    for rec in records:
        company = rec[0] if isinstance(rec[0], str) else ""
        if not company:
            continue
        locations = rec[4] if len(rec) > 4 and isinstance(rec[4], list) else []
        batch = rec[6] if len(rec) > 6 and isinstance(rec[6], list) else []
        industries = rec[7] if len(rec) > 7 and isinstance(rec[7], list) else []
        start_date_val = rec[2] if len(rec) > 2 else None

        cat = classify_industry(industries, group=group)
        loc_str = format_location(locations)
        rec_date = parse_record_date(start_date_val)
        is_today = rec_date == today if rec_date else True  # unknown date → put in today

        bucket = today_data if is_today else recent_data
        if company not in bucket:
            bucket[company] = {"cat": cat, "batch": set(), "loc": loc_str}
        if batch:
            bucket[company]["batch"].update(batch)

    return today_data, recent_data


def _render_section(
    title: str,
    company_data: OrderedDict[str, dict],
    ordered_categories: list[str],
) -> list[str]:
    """Render one section (today or recent) as a list of text lines."""
    grouped: dict[str, list[tuple[str, str, str]]] = OrderedDict()
    for cat in ordered_categories:
        grouped[cat] = []

    for company, data in company_data.items():
        batch_str = "/".join(sorted(data["batch"])) if data["batch"] else "未标注"
        grouped[data["cat"]].append((company, batch_str, data["loc"]))

    lines: list[str] = [title, ""]
    active = [c for c in ordered_categories if grouped[c]]
    if not active:
        lines.append("（无）")
        return lines

    for i, cat in enumerate(active):
        items = grouped[cat]
        lines.append(f"**{cat}**")
        for idx, (name, batch_val, loc) in enumerate(items, 1):
            lines.append(f"【{idx}】{name} — {batch_val}，{loc}")
        if i < len(active) - 1:
            lines.append("---")

    total = sum(len(v) for v in grouped.values())
    lines.extend(["", f"共 {total} 家"])
    return lines


def format_text(records: list[list], group: str = "full", days: int = 10) -> str:
    """Group by industry and return formatted text.

    Always queries 'days' window, then splits into two sections:
      1. 今日新增 — records whose 开始时间 equals today
      2. 近N日其余 — records whose 开始时间 is within the window but not today

    group="simplified" uses 4 categories: 金融, 互联网, 国央企, 其他.
    group="full" uses 9 categories.
    """
    ordered_categories = SIMPLIFIED_ORDERED_CATEGORIES if group == "simplified" else ORDERED_CATEGORIES
    today = date.today()

    today_data, recent_data = _build_company_groups(records, group, today)

    lines: list[str] = []

    # Section 1: today's new records
    today_title = f"🆕 今日新增（{today.isoformat()}）"
    lines.extend(_render_section(today_title, today_data, ordered_categories))

    # Separator between sections
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("")

    # Section 2: recent (not today) records
    if days <= 1:
        recent_title = "近期其余"
    else:
        start = today - timedelta(days=days - 1)
        recent_title = f"📅 近{days}日其余（{start.isoformat()} ~ {today.isoformat()}）"
    lines.extend(_render_section(recent_title, recent_data, ordered_categories))

    # Grand total
    total_all = len(today_data) + len(recent_data)
    lines.extend(["", f"合计：{total_all} 家公司（今日 {len(today_data)} + 近期 {len(recent_data)}）"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch campus recruitment records and format by industry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --today -n 100 -o text\n"
            "  %(prog)s --days 30 -n 200 -o json\n"
            "  %(prog)s --days 10 --group simplified -n 200 -o text\n"
            "  %(prog)s --days 10 --group simplified --where '招聘类型=秋招,秋招提前批,暑期实习,实习' -n 200 -o text\n"
        ),
    )
    parser.add_argument("--today", action="store_true",
                        help="Shortcut for --days 1")
    parser.add_argument("--days", type=int, default=10,
                        help="Time window in days (default: %(default)s)")
    parser.add_argument("-n", "--limit", type=int, default=200,
                        help="Max records to fetch (default: %(default)s)")
    parser.add_argument("-o", "--output", choices=["text", "json"], default="text",
                        help="Output format (default: %(default)s)")
    parser.add_argument("--group", choices=["full", "simplified"], default="full",
                        help="Industry grouping mode: 'full' (9 categories) or 'simplified' (金融/互联网/国央企/其他), default: %(default)s")
    parser.add_argument("--as-user", action="store_true")
    parser.add_argument("--identity", choices=["user", "bot"], default="")
    parser.add_argument("--field", action="append", default=[])
    parser.add_argument("--where", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.today:
        args.days = 1
    if args.as_user and not args.identity:
        args.identity = "user"

    # Default identity for cron environments
    if not args.identity:
        args.identity = "user"
        args.as_user = True

    try:
        records = run_query(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.output == "json":
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        print(format_text(records, group=args.group, days=args.days))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())