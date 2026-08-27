#!/usr/bin/env python3
"""Configure a self-contained Feishu app for an agent notification skill."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request


PROFILES = {
    "codex-notify-hook": {
        "agent": "Codex",
        "app_name": "Codex Notify Agent",
        "description": "Codex notification bot.",
        "env": "~/.codex/feishu-agent.env",
        "source": "codex-notify-hook",
    },
    "claude-code-notify-hook": {
        "agent": "ClaudeCode",
        "app_name": "Claude Code Notify Agent",
        "description": "Claude Code notification bot.",
        "env": "~/.claude/feishu-agent.env",
        "source": "claude-code-notify-hook",
    },
    "opencode-notify-hook": {
        "agent": "OpenCode",
        "app_name": "OpenCode Notify Agent",
        "description": "OpenCode notification bot.",
        "env": "~/.config/opencode/feishu-agent.env",
        "source": "opencode-notify-hook",
    },
    "copilot-notify-hook": {
        "agent": "Copilot",
        "app_name": "Copilot Notify Agent",
        "description": "GitHub Copilot CLI notification bot.",
        "env": "~/.copilot/feishu-agent.env",
        "source": "copilot-notify-hook",
    },
    "cursor-notify-hook": {
        "agent": "Cursor",
        "app_name": "Cursor Notify Agent",
        "description": "Cursor notification bot.",
        "env": "~/.cursor/feishu-agent.env",
        "source": "cursor-notify-hook",
    },
}

ENV_PATHS = [profile["env"] for profile in PROFILES.values()]
COPY_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_DOMAIN",
    "FEISHU_CONNECTION_MODE",
    "FEISHU_HOME_CHANNEL",
    "FEISHU_APPROVAL_RECEIVE_ID",
    "FEISHU_APPROVAL_RECEIVE_ID_TYPE",
)
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
        ]
    },
    "events": {"items": {"tenant": ["im.message.receive_v1"]}},
    "callbacks": {"items": ["card.action.trigger"]},
}


def profile() -> dict[str, str]:
    name = os.environ.get("NOTIFY_SKILL_NAME") or Path(__file__).resolve().parents[1].name
    try:
        return PROFILES[name]
    except KeyError:
        raise SystemExit(f"Unsupported skill directory: {name}")


def read_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = Path(path).expanduser().read_text().splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def receive_id(env: dict[str, str]) -> str:
    return env.get("FEISHU_HOME_CHANNEL") or env.get("FEISHU_APPROVAL_RECEIVE_ID", "")


def complete(env: dict[str, str]) -> bool:
    return bool(env.get("FEISHU_APP_ID") and env.get("FEISHU_APP_SECRET") and receive_id(env))


def write_env(path: str | Path, values: dict[str, str]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    try:
        existing = target.read_text().splitlines()
    except OSError:
        pass
    written: set[str] = set()
    output: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key.startswith("export "):
            key = key[7:].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in values.items() if key not in written)
    target.write_text("\n".join(output) + "\n")
    target.chmod(0o600)


def candidates(target: Path) -> list[Path]:
    override = os.environ.get("FEISHU_REUSE_ENV_PATHS")
    paths = override.split(os.pathsep) if override is not None else ENV_PATHS
    result = [target]
    result.extend(Path(path).expanduser() for path in paths if Path(path).expanduser() != target)
    return result


def find_reusable(target: Path, app_id: str) -> tuple[Path, dict[str, str]] | None:
    for path in candidates(target):
        env = read_env(path)
        if complete(env) and (not app_id or env.get("FEISHU_APP_ID") == app_id):
            return path, env
    return None


def api_base(env: dict[str, str]) -> str:
    return "https://open.larksuite.com" if env.get("FEISHU_DOMAIN") in {"lark", "larksuite"} else "https://open.feishu.cn"


def post(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_connection(env: dict[str, str], agent: str) -> None:
    base = api_base(env)
    token = post(
        f"{base}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": env["FEISHU_APP_ID"], "app_secret": env["FEISHU_APP_SECRET"]},
    )
    if token.get("code") != 0:
        raise RuntimeError(f"tenant token failed: {token.get('code')} {token.get('msg')}")
    card = {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": f"🤖 {agent} · 配置成功"}, "template": "blue"},
        "body": {"elements": [{"tag": "markdown", "content": "通知机器人连接测试成功。"}]},
    }
    result = post(
        f"{base}/open-apis/im/v1/messages?receive_id_type={env.get('FEISHU_APPROVAL_RECEIVE_ID_TYPE', 'chat_id')}",
        {"receive_id": receive_id(env), "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        {"Authorization": f"Bearer {token['tenant_access_token']}"},
    )
    if result.get("code") != 0:
        raise RuntimeError(f"send failed: {result.get('code')} {result.get('msg')}")


def print_manual() -> None:
    print("1. Create an enterprise self-built app in Feishu Open Platform and enable Bot.")
    print("2. Import this permissions/events/callbacks configuration:")
    print(json.dumps(ADDONS, ensure_ascii=False, indent=2))
    print("3. Publish the app, obtain admin approval, and add the bot to the target chat.")
    print("4. Run this script with --live --app-id cli_xxx --home-channel oc_xxx --test.")


def home_channel(args: argparse.Namespace, target_env: dict[str, str]) -> str:
    value = args.home_channel or receive_id(target_env)
    if not value and sys.stdin.isatty():
        value = input("FEISHU_HOME_CHANNEL (target chat_id): ").strip()
    return value


def create_app(args: argparse.Namespace, cfg: dict[str, str], target: Path) -> int:
    channel = home_channel(args, read_env(target))
    if not channel:
        print("No reusable bot configuration found; --home-channel is required before creating an app.", file=sys.stderr)
        return 2
    try:
        import lark_oapi as lark
    except ImportError:
        print("Missing dependency: python3 -m pip install 'lark-oapi>=1.5.5'", file=sys.stderr)
        return 2

    def on_qr_code(info: dict) -> None:
        print(f"Scan or open: {info['url']}")
        print(f"Expires in: {info['expire_in']} seconds")

    kwargs = {
        "on_qr_code": on_qr_code,
        "on_status_change": lambda info: print(f"Status: {info.get('status', '')}"),
        "source": cfg["source"],
        "app_preset": {"name": cfg["app_name"], "desc": cfg["description"]},
        "addons": ADDONS,
    }
    if args.app_id:
        kwargs["app_id"] = args.app_id
    else:
        kwargs["create_only"] = True
    result = lark.register_app(**kwargs)
    values = {
        "FEISHU_APP_ID": result["client_id"],
        "FEISHU_APP_SECRET": result["client_secret"],
        "FEISHU_DOMAIN": (result.get("user_info") or {}).get("tenant_brand", "feishu"),
        "FEISHU_CONNECTION_MODE": "websocket",
        "FEISHU_HOME_CHANNEL": channel,
        "FEISHU_APPROVAL_RECEIVE_ID": channel,
        "FEISHU_APPROVAL_RECEIVE_ID_TYPE": args.receive_id_type,
    }
    write_env(target, values)
    print(f"Created/updated app {values['FEISHU_APP_ID']} and wrote {target} (mode 600).")
    if args.test:
        if sys.stdin.isatty():
            input(f"Add the bot to {channel}, then press Enter to test delivery: ")
        test_connection(read_env(target), cfg["agent"])
        print("Notification bot test succeeded.")
    return 0


def main() -> int:
    cfg = profile()
    parser = argparse.ArgumentParser(description=f"Configure {cfg['agent']} Feishu notifications.")
    parser.add_argument("--live", action="store_true", help="create/select an app when no reusable config exists")
    parser.add_argument("--manual", action="store_true", help="print manual setup steps")
    parser.add_argument("--new", action="store_true", help="skip automatic reuse and create a separate app")
    parser.add_argument("--app-id", default="", help="reuse or select this existing app")
    parser.add_argument("--home-channel", default="", help="target chat_id, required for a new setup")
    parser.add_argument("--receive-id-type", default="chat_id")
    parser.add_argument("--env-out", default=cfg["env"], help="this agent's independent env file")
    parser.add_argument("--test", action="store_true", help="send one connection-test card")
    args = parser.parse_args()
    if args.new and args.app_id:
        parser.error("--new and --app-id are mutually exclusive")
    if not args.env_out:
        parser.error("--env-out must name this agent's env file")
    target = Path(args.env_out).expanduser()

    if args.manual:
        print_manual()
        return 0
    if not args.new:
        found = find_reusable(target, args.app_id)
        if found:
            source, env = found
            values = {key: env[key] for key in COPY_KEYS if env.get(key)}
            values["FEISHU_HOME_CHANNEL"] = receive_id(env)
            values["FEISHU_APPROVAL_RECEIVE_ID"] = receive_id(env)
            values.setdefault("FEISHU_APPROVAL_RECEIVE_ID_TYPE", "chat_id")
            if args.home_channel:
                values["FEISHU_HOME_CHANNEL"] = args.home_channel
                values["FEISHU_APPROVAL_RECEIVE_ID"] = args.home_channel
                values["FEISHU_APPROVAL_RECEIVE_ID_TYPE"] = args.receive_id_type
            write_env(target, values)
            print(f"Reused app {env['FEISHU_APP_ID']} from {source}; wrote independent {target} (mode 600).")
            if args.test:
                test_connection(read_env(target), cfg["agent"])
                print("Notification bot test succeeded.")
            return 0
    if not args.live:
        print("No reusable complete bot configuration found.")
        print(json.dumps({"app_preset": {"name": cfg["app_name"], "desc": cfg["description"]}, "addons": ADDONS}, ensure_ascii=False, indent=2))
        print("Run again with --live --home-channel <chat_id>; add --test to verify delivery.")
        return 0
    try:
        return create_app(args, cfg, target)
    except Exception as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
