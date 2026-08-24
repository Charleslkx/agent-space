#!/usr/bin/env python3
"""Send Copilot CLI completion and attention events to Feishu."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request


AGENT = "Copilot"
ENV_PATH = Path(os.environ.get("FEISHU_ENV", "~/.copilot/feishu-agent.env")).expanduser()


def debug(message: str) -> None:
    if os.environ.get("NOTIFY_DEBUG") == "1":
        print(f"[notify] {message}", file=sys.stderr)


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}


def is_focused() -> bool:
    if os.environ.get("NOTIFY_FORCE") == "1" or sys.platform != "darwin":
        return False
    owner = os.environ.get("__CFBundleIdentifier")
    if not owner:
        return False
    try:
        asn = subprocess.run(
            ["lsappinfo", "front"], capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
        info = subprocess.run(
            ["lsappinfo", "info", "-only", "bundleid", asn],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r'"CFBundleIdentifier"="([^"]+)"', info)
    return bool(match and match.group(1) == owner)


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in ENV_PATH.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    except OSError:
        pass
    return values


def post(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def api_base(env: dict[str, str]) -> str:
    if env.get("FEISHU_DOMAIN") in {"lark", "larksuite"}:
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


def card(project: str, content: str, kind: str) -> dict:
    attention = kind == "attention"
    title = f"⚠️ {AGENT} · 需要注意" if attention else f"🤖 {AGENT} · 任务完成"
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "orange" if attention else "blue",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**Agent**\n{AGENT}\n\n**Project**\n{project}"
                        f"\n\n**Content**\n{content}"
                    ),
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
                {
                    "tag": "markdown",
                    "content": time.strftime("<font color='grey'>🕒 %Y-%m-%d %H:%M:%S</font>"),
                    "text_align": "left",
                    "text_size": "normal_v2",
                },
            ],
        },
    }


def send_app(env: dict[str, str], message_card: dict) -> bool:
    app_id = env.get("FEISHU_APP_ID")
    app_secret = env.get("FEISHU_APP_SECRET")
    receive_id = env.get("FEISHU_HOME_CHANNEL") or env.get("FEISHU_APPROVAL_RECEIVE_ID")
    receive_type = env.get("FEISHU_APPROVAL_RECEIVE_ID_TYPE", "chat_id")
    if not (app_id and app_secret and receive_id):
        return False
    base = api_base(env)
    token = post(
        f"{base}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    if token.get("code") != 0:
        return False
    result = post(
        f"{base}/open-apis/im/v1/messages?receive_id_type={receive_type}",
        {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(message_card, ensure_ascii=False),
        },
        {"Authorization": f"Bearer {token['tenant_access_token']}"},
    )
    return result.get("code") == 0


def send_webhook(message_card: dict) -> bool:
    url = os.environ.get("LARK_WEBHOOK_URL")
    if not url:
        return False
    payload = {"msg_type": "interactive", "card": message_card}
    secret = os.environ.get("LARK_WEBHOOK_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(
            hmac.new(f"{timestamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        ).decode()
    post(url, payload)
    return True


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "notification"
    payload = read_payload()
    project = Path(payload.get("cwd") or os.getcwd()).name
    kind = "attention" if event == "notification" else "done"
    content = payload.get("message") or ("需要你的关注" if kind == "attention" else "任务已完成")

    if is_focused():
        debug("焦点在 Copilot 会话窗口，跳过飞书")
        return
    if os.environ.get("NOTIFY_DRY_RUN") == "1":
        debug(f"dry-run kind={kind} project={project} content={content}")
        return

    message_card = card(project, content, kind)
    try:
        if send_app(load_env(), message_card):
            debug("飞书应用卡片已发送")
            return
    except Exception as error:  # Hook failures must never interrupt Copilot.
        debug(f"飞书应用发送失败: {error}")
    try:
        if send_webhook(message_card):
            debug("飞书 webhook 卡片已发送")
    except Exception as error:
        debug(f"飞书 webhook 发送失败: {error}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # The hook always exits successfully.
        debug(f"通知 hook 异常: {error}")
