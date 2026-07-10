#!/usr/bin/env python3
"""Create (or select) a Feishu/Lark agent app for Claude Code notifications.

一键创建流程：扫码后飞书创建或选择自建应用，SDK 返回 App ID / App Secret，
脚本把凭证直接写入 --env-out 指向的 feishu-agent.env（默认 ~/.claude/feishu-agent.env）。

共用一个应用时：--live 时在扫码页选择同一个已有应用（或用 --app-id 直接指定），
SDK 会返回该应用的 App Secret，脚本据此生成本 agent 自己的 env（不共享文件、不软链）。

Dry-run is dependency-free. Live mode requires:
    python3 -m pip install 'lark-oapi>=1.5.5'
"""

from __future__ import annotations

import argparse
import json
import os
import sys


ADDONS = {
    "scopes": {
        "tenant": [
            "im:message.p2p_msg:readonly",
            "im:message.group_at_msg:readonly",
            "im:message:send_as_bot",
            "im:message:update",
            "im:resource",
            "im:chat:read",
            "cardkit:card:read",
            "cardkit:card:write",
            "application:bot.basic_info:read",
        ],
    },
    "events": {"items": {"tenant": ["im.message.receive_v1"]}},
    "callbacks": {"items": ["card.action.trigger"]},
}

APP_PRESET = {
    "name": "Claude Code Notify Agent",
    "desc": "Claude Code notification bot.",
}

DEFAULT_ENV_OUT = os.path.expanduser("~/.claude/feishu-agent.env")

# 凭证类键由本脚本覆盖，其它键（如 FEISHU_HOME_CHANNEL）保留
CRED_KEYS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DOMAIN", "FEISHU_CONNECTION_MODE")


def print_manual_setup() -> None:
    print("Manual Feishu app setup:")
    print("1. Create an enterprise self-built app in Feishu Open Platform.")
    print("2. Enable the Bot capability.")
    print("3. Bulk-import this permissions/events/callbacks config:")
    print(json.dumps(ADDONS, ensure_ascii=False, indent=4))
    print("4. Use WebSocket long connection unless you already have a public webhook.")
    print("5. Publish a new app version and wait for admin approval.")


def print_dry_run() -> None:
    print("Live mode will call lark_oapi.register_app with:")
    print(json.dumps({"app_preset": APP_PRESET, "addons": ADDONS, "create_only": True}, ensure_ascii=False, indent=4))


def write_env(path: str, values: dict[str, str]) -> None:
    """Merge credential keys into env file, preserving other keys. chmod 600."""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in values.items():
        if key not in seen:
            out.append(f"{key}={val}")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)


def run_live(app_id: str, env_out: str) -> int:
    try:
        import lark_oapi as lark
    except ImportError:
        print("Missing dependency: python3 -m pip install 'lark-oapi>=1.5.5'", file=sys.stderr)
        return 2

    def on_qr_code(info: dict) -> None:
        print(f"Scan or open: {info['url']}")
        print(f"Expires in: {info['expire_in']} seconds")

    def on_status_change(info: dict) -> None:
        status = info.get("status", "")
        interval = info.get("interval")
        print(f"Status: {status}" + (f" interval={interval}" if interval else ""))

    kwargs = {
        "on_qr_code": on_qr_code,
        "on_status_change": on_status_change,
        "source": "claude-code-notify-hook",
        "app_preset": APP_PRESET,
        "addons": ADDONS,
    }
    if app_id:
        kwargs["app_id"] = app_id
    else:
        kwargs["create_only"] = True

    result = lark.register_app(**kwargs)
    brand = (result.get("user_info") or {}).get("tenant_brand", "feishu")
    values = {
        "FEISHU_APP_ID": result["client_id"],
        "FEISHU_APP_SECRET": result["client_secret"],
        "FEISHU_DOMAIN": brand,
        "FEISHU_CONNECTION_MODE": "websocket",
    }
    print("Created/updated Feishu app.")
    for k, v in values.items():
        print(f"{k}={v}" if k != "FEISHU_APP_SECRET" else f"{k}=***")
    if env_out:
        write_env(env_out, values)
        print(f"Wrote credentials to {os.path.expanduser(env_out)} (mode 600).")
        print("提醒：FEISHU_HOME_CHANNEL 仍需机器人私聊 /set-home 或手工填入。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Feishu agent app for Claude Code notify hooks.")
    parser.add_argument("--live", action="store_true", help="call Feishu SDK and wait for QR approval")
    parser.add_argument("--manual", action="store_true", help="print manual setup checklist")
    parser.add_argument("--app-id", default="", help="select/update an existing app (共用同一应用) instead of creating a new one")
    parser.add_argument("--env-out", default=DEFAULT_ENV_OUT, help=f"write fetched credentials here (default {DEFAULT_ENV_OUT}); empty string to skip")
    args = parser.parse_args()

    if args.manual:
        print_manual_setup()
        return 0
    if not args.live:
        print_dry_run()
        return 0
    return run_live(args.app_id, args.env_out)


if __name__ == "__main__":
    raise SystemExit(main())
