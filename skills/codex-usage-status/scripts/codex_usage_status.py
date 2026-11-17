#!/usr/bin/env python3
"""Read Codex account rate-limit windows through the local app-server."""

from __future__ import annotations

import argparse
import json
import selectors
import subprocess
import sys
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show current Codex usage and rate-limit reset times."
    )
    parser.add_argument(
        "--timezone",
        help="IANA timezone name such as Asia/Shanghai (default: system timezone)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="query timeout in seconds (default: 30)",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def timezone_from_name(name: str | None):
    if not name:
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"unknown timezone: {name}") from exc


class AppServer:
    def __init__(self, timeout: float) -> None:
        self.deadline = time.monotonic() + timeout
        try:
            self.process = subprocess.Popen(
                ["codex", "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("codex executable was not found") from exc
        self.selector = selectors.DefaultSelector()
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.stderr_lines: list[str] = []

    def close(self) -> None:
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def request(self, request_id: int, method: str, params: Any) -> dict[str, Any]:
        assert self.process.stdin is not None
        message = {"id": request_id, "method": method, "params": params}
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError(self._failure("Codex app-server exited unexpectedly")) from exc

        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(self._failure(f"query timed out waiting for {method}"))
            events = self.selector.select(remaining)
            if not events:
                raise RuntimeError(self._failure(f"query timed out waiting for {method}"))
            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    try:
                        self.selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    continue
                if key.data == "stderr":
                    self.stderr_lines.append(line.strip())
                    self.stderr_lines = self.stderr_lines[-5:]
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("id") != request_id:
                    continue
                if "error" in payload:
                    raise RuntimeError(f"{method} failed: {payload['error']}")
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"{method} returned an invalid result")
                return result

    def _failure(self, message: str) -> str:
        details = "; ".join(line for line in self.stderr_lines if line)
        return f"{message}: {details}" if details else message


def window_name(duration_minutes: int | None, slot: str) -> str:
    if duration_minutes == 300:
        return "5 小时窗口"
    if duration_minutes == 10080:
        return "每周窗口"
    if duration_minutes is None:
        return slot
    if duration_minutes % 1440 == 0:
        return f"{duration_minutes // 1440} 天窗口"
    if duration_minutes % 60 == 0:
        return f"{duration_minutes // 60} 小时窗口"
    return f"{duration_minutes} 分钟窗口"


def countdown(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds <= 0:
        return "已到刷新时间"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    if minutes or hours or days:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


def normalize(result: dict[str, Any], tz) -> dict[str, Any]:
    now_epoch = int(time.time())
    now = datetime.fromtimestamp(now_epoch, tz)
    snapshots = result.get("rateLimitsByLimitId")
    if not isinstance(snapshots, dict) or not snapshots:
        fallback = result.get("rateLimits")
        snapshots = (
            {fallback.get("limitId", "codex"): fallback}
            if isinstance(fallback, dict)
            else {}
        )

    limits = []
    for limit_id, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        windows = []
        for slot in ("primary", "secondary"):
            window = snapshot.get(slot)
            if not isinstance(window, dict):
                continue
            duration = window.get("windowDurationMins")
            reset_epoch = window.get("resetsAt")
            used = window.get("usedPercent")
            reset_dt = (
                datetime.fromtimestamp(reset_epoch, tz)
                if isinstance(reset_epoch, (int, float))
                else None
            )
            windows.append(
                {
                    "slot": slot,
                    "name": window_name(duration, slot),
                    "duration_minutes": duration,
                    "used_percent": used,
                    "remaining_percent": (
                        max(0, 100 - used)
                        if isinstance(used, (int, float))
                        else None
                    ),
                    "resets_at_epoch": reset_epoch,
                    "resets_at": reset_dt.isoformat() if reset_dt else None,
                    "resets_in_seconds": (
                        max(0, int(reset_epoch - now_epoch))
                        if isinstance(reset_epoch, (int, float))
                        else None
                    ),
                }
            )
        limits.append(
            {
                "limit_id": limit_id,
                "limit_name": snapshot.get("limitName"),
                "plan_type": snapshot.get("planType"),
                "reached_type": snapshot.get("rateLimitReachedType"),
                "windows": windows,
            }
        )

    top_snapshot = result.get("rateLimits")
    credits = top_snapshot.get("credits", {}) if isinstance(top_snapshot, dict) else {}
    reset_credits = result.get("rateLimitResetCredits") or {}
    return {
        "queried_at": now.isoformat(),
        "timezone": str(tz),
        "limits": limits,
        "credits": {
            "has_credits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        },
        "available_full_resets": reset_credits.get("availableCount"),
    }


def print_human(data: dict[str, Any], tz) -> None:
    queried = datetime.fromisoformat(data["queried_at"]).astimezone(tz)
    print(f"查询时间：{queried.strftime('%Y-%m-%d %H:%M:%S %Z (%z)')}")
    print(f"时区：{data['timezone']}")
    for limit in data["limits"]:
        title = limit["limit_name"] or limit["limit_id"]
        plan = f"，套餐 {limit['plan_type']}" if limit["plan_type"] else ""
        print(f"\n限额：{title}{plan}")
        for window in limit["windows"]:
            used = window["used_percent"]
            remaining = window["remaining_percent"]
            usage = (
                f"已用 {used:g}%，剩余 {remaining:g}%"
                if isinstance(used, (int, float))
                else "用量未知"
            )
            if window["resets_at"]:
                reset_dt = datetime.fromisoformat(window["resets_at"]).astimezone(tz)
                reset_text = reset_dt.strftime("%Y-%m-%d %H:%M:%S %Z (%z)")
                remaining_text = countdown(window["resets_in_seconds"])
                print(
                    f"- {window['name']}：{usage}；"
                    f"刷新 {reset_text}（{remaining_text}）"
                )
            else:
                print(f"- {window['name']}：{usage}；刷新时间未知")

    credits = data["credits"]
    if credits["unlimited"]:
        print("\nCredits：无限")
    elif credits["has_credits"] is not None:
        print(f"\nCredits：{credits['balance'] or '0'}")
    if data["available_full_resets"] is not None:
        print(f"可用完整重置：{data['available_full_resets']} 次（未使用）")


def main() -> int:
    args = parse_args()
    server = None
    try:
        tz = timezone_from_name(args.timezone)
        server = AppServer(args.timeout)
        server.request(
            1,
            "initialize",
            {
                "clientInfo": {"name": "codex-usage-status", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        result = server.request(2, "account/rateLimits/read", None)
        data = normalize(result, tz)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print_human(data, tz)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(main())
