#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import sys
import urllib.request

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from feishu_approval_common import PENDING_DIR, RESULT_DIR, ensure_state_dirs, load_env, now, read_json, set_env_value, write_json


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


def send_text(chat_id: str, text: str) -> None:
    env = load_env()
    token = tenant_token(env)
    post_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        {"Authorization": f"Bearer {token}"},
    )


def update_card(message_id: str, card: dict) -> None:
    env = load_env()
    token = tenant_token(env)
    post_json(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
        {"card": card},
        {"Authorization": f"Bearer {token}"},
    )


def resolved_card(pending: dict, decision: str) -> dict:
    agent = str(pending.get("agent") or "Codex")
    project = str(pending.get("project") or "")
    content = str(pending.get("content") or "")
    status = "已批准" if decision == "allow" else "已拒绝"
    template = "green" if decision == "allow" else "red"
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{agent} 权限申请"},
            "subtitle": {"tag": "plain_text", "content": f"{project} · {status}"},
            "template": template,
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**Project**\\n{project}\\n\\n**Content**\\n{content}\\n\\n**Decision**\\n{status}",
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
                {
                    "tag": "markdown",
                    "content": f"Approval ID: `{pending.get('approval_id', '')}`",
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
            ],
        },
    }


def handle_card(event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    value = {}
    if event.event and event.event.action and isinstance(event.event.action.value, dict):
        value = event.event.action.value
    approval_id = str(value.get("approval_id") or "")
    decision = str(value.get("decision") or "")
    if approval_id and decision:
        operator = event.event.operator.open_id if event.event and event.event.operator else ""
        write_json(RESULT_DIR / f"{approval_id}.json", {
            "approval_id": approval_id,
            "decision": decision,
            "operator_open_id": operator,
            "decided_at": now(),
        })
        pending_path = PENDING_DIR / f"{approval_id}.json"
        if pending_path.exists():
            pending = read_json(pending_path)
            message_id = str(pending.get("message_id") or "")
            if message_id:
                try:
                    update_card(message_id, resolved_card(pending, decision))
                except Exception as exc:
                    print(json.dumps({"warning": "update_card_failed", "approval_id": approval_id, "error": str(exc)}, ensure_ascii=False), flush=True)
        content = "Recorded: allow" if decision == "allow" else "Recorded: deny"
    else:
        content = "Missing approval_id or decision"
    resp = P2CardActionTriggerResponse()
    resp.toast = CallBackToast({"type": "success", "content": content})
    return resp


def handle_message(event: P2ImMessageReceiveV1) -> None:
    if not event.event or not event.event.message:
        return
    msg = event.event.message
    if msg.message_type != "text":
        return
    try:
        text = json.loads(msg.content or "{}").get("text", "").strip()
    except json.JSONDecodeError:
        text = ""
    if text != "/set-home":
        return
    chat_id = msg.chat_id or ""
    if not chat_id:
        return
    set_env_value("FEISHU_HOME_CHANNEL", chat_id)
    send_text(chat_id, f"Codex approval home set: {chat_id}")
    print(json.dumps({"status": "home_set", "chat_id": chat_id}, ensure_ascii=False), flush=True)


def main() -> int:
    env = load_env()
    ensure_state_dirs()
    app_id = env.get("FEISHU_APP_ID", "")
    app_secret = env.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        print("FEISHU_APP_ID and FEISHU_APP_SECRET are required", file=sys.stderr)
        return 2

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .register_p2_card_action_trigger(handle_card)
        .build()
    )
    client = lark.ws.Client(app_id, app_secret, event_handler=handler)
    print(json.dumps({"status": "starting", "app_id": app_id}, ensure_ascii=False), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    client.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
