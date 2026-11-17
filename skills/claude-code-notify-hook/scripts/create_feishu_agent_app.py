#!/usr/bin/env python3
"""Configure a self-contained Feishu app for an agent notification skill."""

from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import inspect
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit


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
# Notification-only apps need bot message sending, not user identity or callbacks.
ADDONS = {"preset": False, "scopes": {"tenant": ["im:message:send_as_bot"]}}


def missing_registration_parameters(function) -> list[str]:
    required = {"on_qr_code", "on_status_change", "source", "app_preset", "addons", "app_id", "create_only"}
    if not callable(function):
        return sorted(required)
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return sorted(required)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return []
    return sorted(required - set(parameters))


def check_environment() -> dict:
    """Read-only capability checks; never query or print app credentials."""
    sdk = {"available": False, "register_app": False, "version": None, "supports_setup": False}
    try:
        import lark_oapi as lark
        sdk["available"] = True
        sdk["register_app"] = callable(getattr(lark, "register_app", None))
        missing = missing_registration_parameters(getattr(lark, "register_app", None))
        sdk["supports_setup"] = not missing
        if missing:
            sdk["missing_parameters"] = missing
        try:
            sdk["version"] = importlib.metadata.version("lark-oapi")
        except importlib.metadata.PackageNotFoundError:
            pass
    except ImportError:
        pass
    cli = {"available": False, "version": None, "supports_app_creation": False}
    executable = shutil.which("lark-cli")
    if executable:
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
            help_result = subprocess.run([executable, "config", "init", "--help"], capture_output=True, text=True, timeout=10)
            cli["available"] = version.returncode == 0
            # Version output is reduced to the release number; omit paths and identities.
            import re
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\b", version.stdout)
            cli["version"] = match.group(0) if match else None
            cli["supports_app_creation"] = help_result.returncode == 0 and "--new" in help_result.stdout
        except (OSError, subprocess.TimeoutExpired):
            cli["error"] = "capability check failed or timed out"
    return {"python": ".".join(map(str, sys.version_info[:3])), "sdk": sdk,
            "lark_cli": cli, "ready_for_sdk_creation": sdk["supports_setup"]}


def configure_manually(args: argparse.Namespace, cfg: dict[str, str], target: Path) -> int:
    if not sys.stdin.isatty():
        print("--configure requires an interactive terminal; credentials are never command arguments.", file=sys.stderr)
        return 2
    app_id = input("App ID: ").strip()
    secret = getpass.getpass("App Secret: ").strip()
    channel = input("Target chat_id: ").strip()
    brand = input("Brand [feishu/lark, default feishu]: ").strip() or "feishu"
    if not app_id or not secret or not channel or brand not in {"feishu", "lark"}:
        print("All credentials and a valid brand are required; no file was changed.", file=sys.stderr)
        return 2
    write_env(target, {"FEISHU_APP_ID": app_id, "FEISHU_APP_SECRET": secret,
                      "FEISHU_HOME_CHANNEL": channel, "FEISHU_APPROVAL_RECEIVE_ID": channel,
                      "FEISHU_APPROVAL_RECEIVE_ID_TYPE": "chat_id", "FEISHU_DOMAIN": brand})
    print("Wrote independent notification configuration (mode 600).")
    if args.test:
        test_connection(read_env(target), cfg["agent"])
        print("Notification bot test succeeded.")
    return 0



def configure_webhook(target: Path) -> int:
    if not sys.stdin.isatty():
        print("webhook configuration requires an interactive terminal", file=sys.stderr)
        return 2
    url = getpass.getpass("Webhook URL (hidden): ").strip()
    secret = getpass.getpass("Webhook signing secret (optional, hidden): ").strip()
    parsed = urlsplit(url)
    prefix = "/open-apis/bot/v2/hook/"
    if parsed.scheme != "https" or parsed.hostname not in {"open.feishu.cn", "open.larksuite.com"} or not parsed.path.startswith(prefix) or not parsed.path[len(prefix):]:
        print("A valid Feishu/Lark HTTPS webhook URL is required; no file was changed.", file=sys.stderr)
        return 2
    write_env(target, {"LARK_WEBHOOK_URL": url, "LARK_WEBHOOK_SECRET": secret})
    print("Wrote private webhook configuration (mode 600).")
    return 0


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


def credential_target(path: str | Path) -> Path:
    target = Path(path).expanduser()
    skill_parent = Path(__file__).resolve().parents[2]
    protected = [skill_parent]
    repository = skill_parent.parent
    if (repository / "mcp").is_dir() and (repository / "skills").is_dir():
        protected.append(repository / "mcp")
    resolved = target.resolve()
    if any(resolved == base or base in resolved.parents for base in protected):
        raise ValueError("credential files must be outside distributed MCP and Skill source directories")
    if target.is_symlink():
        raise ValueError("refusing to replace a symlinked credential file")
    if target.exists() and not target.is_file():
        raise ValueError("credential destination must be a regular file")
    return target


def write_env(path: str | Path, values: dict[str, str]) -> None:
    target = credential_target(path)
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
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("environment values must be single-line")
    if target.is_symlink():
        raise ValueError("refusing to replace a symlinked credential file")
    # Create private from the first byte, then atomically replace the destination.
    descriptor, temporary = tempfile.mkstemp(prefix=".feishu-env-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("\n".join(output) + "\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)



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
        "header": {"title": {"tag": "plain_text", "content": f"{agent} · 配置成功"}, "template": "blue"},
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
    print("2. Grant the application im:message:send_as_bot; no event or button callback is needed:")
    print(json.dumps(ADDONS, ensure_ascii=False, indent=2))
    print("3. Publish the app, obtain admin approval, and add the bot to the target chat.")
    print("4. Run this script with --configure in a terminal to store credentials without changing source files.")


def home_channel(args: argparse.Namespace, target_env: dict[str, str]) -> str:
    value = args.home_channel or receive_id(target_env)
    if not value and sys.stdin.isatty():
        value = input("FEISHU_HOME_CHANNEL (target chat_id): ").strip()
    return value


def create_app(args: argparse.Namespace, cfg: dict[str, str], target: Path) -> int:
    target = credential_target(target)
    channel = home_channel(args, read_env(target))
    if not channel or channel.startswith(("your-", "REPLACE_")):
        print("No usable target chat_id found; --home-channel is required before creating an app.", file=sys.stderr)
        return 2
    try:
        import lark_oapi as lark
    except ImportError:
        print("Missing dependency: install requirements-setup.txt with this Python interpreter.", file=sys.stderr)
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
    if missing_registration_parameters(getattr(lark, "register_app", None)):
        print("SDK lacks required setup parameters; install requirements-setup.txt with this Python interpreter.", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, probe = tempfile.mkstemp(prefix=".feishu-env-", dir=target.parent)
    os.close(descriptor)
    os.unlink(probe)
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
    print("Created/updated application; wrote independent configuration (mode 600).")
    if args.test:
        if sys.stdin.isatty():
            input(f"Add the bot to {channel}, then press Enter to test delivery: ")
        test_connection(read_env(target), cfg["agent"])
        print("Notification bot test succeeded.")
    return 0


def main() -> int:
    subprocess.run(['bash', str(Path(__file__).with_name('check-python.sh')), '--runtime'],
                   check=True, stdout=sys.stderr, env={**os.environ, 'PYTHON_BIN': sys.executable})
    cfg = profile()
    parser = argparse.ArgumentParser(description=f"Configure {cfg['agent']} Feishu notifications.")
    parser.add_argument("--check", action="store_true", help="report SDK and lark-cli capabilities without creating apps")
    parser.add_argument("--configure-webhook", action="store_true", help="enter Codex fallback webhook privately")
    parser.add_argument("--configure", action="store_true", help="enter existing credentials interactively; no SDK needed")
    parser.add_argument("--live", action="store_true", help="create/select an app when no reusable config exists")
    parser.add_argument("--manual", action="store_true", help="print manual setup steps")
    parser.add_argument("--new", action="store_true", help="skip automatic reuse and create a separate app")
    parser.add_argument("--app-id", default="", help="reuse or select this existing app")
    parser.add_argument("--home-channel", default="", help="target chat_id, required for a new setup")
    parser.add_argument("--receive-id-type", default="chat_id")
    parser.add_argument("--env-out", default=cfg["env"], help="this agent's independent env file")
    parser.add_argument("--test", action="store_true", help="send one connection-test card")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check_environment(), ensure_ascii=False, indent=2))
        return 0
    if args.configure_webhook and (args.live or args.new or args.app_id or args.configure or args.test):
        parser.error("--configure-webhook cannot be combined with app or test operations")
    if args.configure and (args.live or args.new or args.app_id):
        parser.error("--configure cannot be combined with application creation flags")
    if args.live and args.app_id.startswith(("your-", "REPLACE_")):
        parser.error("replace the application ID placeholder before updating an app")
    if args.new and args.app_id:
        parser.error("--new and --app-id are mutually exclusive")
    if not args.env_out:
        parser.error("--env-out must name this agent's env file")
    try:
        target = credential_target(args.env_out)
    except ValueError as error:
        parser.error(str(error))

    if args.configure_webhook:
        if cfg["agent"] != "Codex":
            parser.error("persistent webhook files are supported only by the Codex runtime")
        return configure_webhook(target.with_name("feishu-webhook.env"))
    if args.configure:
        return configure_manually(args, cfg, target)
    if args.manual:
        print_manual()
        return 0
    if not args.new and not (args.live and args.app_id):
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
            print("Reused application; wrote independent configuration (mode 600).")
            if args.test:
                test_connection(read_env(target), cfg["agent"])
                print("Notification bot test succeeded.")
            return 0
    if not args.live:
        print("No reusable complete bot configuration found.")
        print(json.dumps({"app_preset": {"name": cfg["app_name"], "desc": cfg["description"]}, "addons": ADDONS}, ensure_ascii=False, indent=2))
        print("Run again with --live --home-channel your-chat-id, or --configure; --test sends a card.")
        return 2 if args.test else 0
    try:
        return create_app(args, cfg, target)
    except Exception as error:
        print(f"Setup failed: {type(error).__name__}; check local configuration and permissions.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # API responses can contain tenant or chat identifiers; keep diagnostics private.
        print(f"Configuration failed: {type(error).__name__}; check local configuration and permissions.", file=sys.stderr)
        raise SystemExit(3)
