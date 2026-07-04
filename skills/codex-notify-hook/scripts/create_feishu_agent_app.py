#!/usr/bin/env python3
"""Create a Feishu/Lark agent app for Codex notifications.

Dry-run is dependency-free. Live mode requires:
    python3 -m pip install 'lark-oapi>=1.5.5'
"""

from __future__ import annotations

import argparse
import json
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
    "name": "Codex Notify Agent",
    "desc": "Codex notification and approval bot.",
}


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


def run_live(app_id: str) -> int:
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
        "source": "codex-notify-hook",
        "app_preset": APP_PRESET,
        "addons": ADDONS,
    }
    if app_id:
        kwargs["app_id"] = app_id
    else:
        kwargs["create_only"] = True

    result = lark.register_app(**kwargs)
    print("Created/updated Feishu app.")
    print(f"FEISHU_APP_ID={result['client_id']}")
    print(f"FEISHU_APP_SECRET={result['client_secret']}")
    brand = (result.get("user_info") or {}).get("tenant_brand", "feishu")
    print(f"FEISHU_DOMAIN={brand}")
    print("FEISHU_CONNECTION_MODE=websocket")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Feishu agent app for Codex notify hooks.")
    parser.add_argument("--live", action="store_true", help="call Feishu SDK and wait for QR approval")
    parser.add_argument("--manual", action="store_true", help="print manual setup checklist")
    parser.add_argument("--app-id", default="", help="update an existing app instead of creating a new one")
    args = parser.parse_args()

    if args.manual:
        print_manual_setup()
        return 0
    if not args.live:
        print_dry_run()
        return 0
    return run_live(args.app_id)


if __name__ == "__main__":
    raise SystemExit(main())
