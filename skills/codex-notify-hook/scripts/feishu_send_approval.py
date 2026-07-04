#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from feishu_approval_common import PENDING_DIR, ensure_state_dirs, load_env, now, write_json


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def tenant_token(env: dict[str, str]) -> str:
    resp = post_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": env["FEISHU_APP_ID"], "app_secret": env["FEISHU_APP_SECRET"]},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"tenant token failed: {resp.get('code')} {resp.get('msg')}")
    return resp["tenant_access_token"]


def card(args: argparse.Namespace, *, resolved: bool = False, decision: str | None = None) -> dict:
    status = "待处理" if not resolved else ("已批准" if decision == "allow" else "已拒绝")
    template = "orange" if not resolved else ("green" if decision == "allow" else "red")
    footer = f"Approval ID: `{args.approval_id}`"
    card_body: dict = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{args.agent} 权限申请"},
            "subtitle": {"tag": "plain_text", "content": f"{args.project} · {status}"},
            "template": template,
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**Project**\\n{args.project}\\n\\n**Content**\\n{args.content}",
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
                {
                    "tag": "markdown",
                    "content": footer,
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
            ],
        },
    }
    if not resolved:
        card_body["body"]["elements"].append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Allow once"},
                        "type": "primary",
                        "value": {"approval_id": args.approval_id, "decision": "allow"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Deny"},
                        "type": "danger",
                        "value": {"approval_id": args.approval_id, "decision": "deny"},
                    },
                ],
            }
        )
    return card_body


def remember_pending(args: argparse.Namespace, *, receive_id: str, receive_id_type: str, message_id: str) -> None:
    write_json(PENDING_DIR / f"{args.approval_id}.json", {
        "approval_id": args.approval_id,
        "agent": args.agent,
        "project": args.project,
        "content": args.content,
        "receive_id": receive_id,
        "receive_id_type": receive_id_type,
        "message_id": message_id,
        "created_at": now(),
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="Codex")
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--content", required=True)
    args = parser.parse_args()

    env = load_env()
    receive_id = env.get("FEISHU_APPROVAL_RECEIVE_ID") or env.get("FEISHU_HOME_CHANNEL")
    receive_id_type = env.get("FEISHU_APPROVAL_RECEIVE_ID_TYPE", "chat_id")
    if not receive_id:
        print("FEISHU_APPROVAL_RECEIVE_ID or FEISHU_HOME_CHANNEL is required", file=sys.stderr)
        return 2

    ensure_state_dirs()
    token = tenant_token(env)
    resp = post_json(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card(args), ensure_ascii=False, separators=(",", ":")),
            "uuid": args.approval_id,
        },
        {"Authorization": f"Bearer {token}"},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"send message failed: {resp.get('code')} {resp.get('msg')}")
    message_id = str((resp.get("data") or {}).get("message_id") or "")
    if message_id:
        remember_pending(args, receive_id=receive_id, receive_id_type=receive_id_type, message_id=message_id)
    print(json.dumps({"approval_id": args.approval_id, "sent": True, "message_id": message_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
