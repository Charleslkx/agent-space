#!/usr/bin/env python3
"""OAuth-protected remote wrapper for the official Lark CLI."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

LOGGER = logging.getLogger("lark_cli_mcp")

BASE_URL_ENV = "LARK_CLI_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "LARK_CLI_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "LARK_CLI_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "LARK_CLI_MCP_GITHUB_USERS"
JWT_SIGNING_KEY_ENV = "LARK_CLI_MCP_JWT_SIGNING_KEY"
STORAGE_KEY_ENV = "LARK_CLI_MCP_STORAGE_KEY"
REDIS_PASSWORD_ENV = "LARK_CLI_MCP_REDIS_PASSWORD"
REDIS_HOST_ENV = "LARK_CLI_MCP_REDIS_HOST"
CLI_ENV = "LARK_CLI_MCP_CLI_PATH"
STATE_DIR_ENV = "LARK_CLI_MCP_STATE_DIR"
UPDATE_CHECK_ENV = "LARK_CLI_MCP_UPDATE_CHECK"

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Alark-cli/oauth/callback"
ALLOWED_CLIENT_REDIRECT_URIS = [
    "https://chatgpt.com/connector/oauth/*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    WORKBUDDY_REDIRECT_URI,
    "http://localhost:*",
    "http://127.0.0.1:*",
]

MAX_ARGS = 128
MAX_ARG_BYTES = 16 * 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 180
MAX_EVENT_TIMEOUT_SECONDS = 150

BLOCKED_COMMANDS = {
    "auth": "authentication is managed by the deployment administrator",
    "config": "server configuration is managed by the deployment administrator",
    "doctor": "diagnoses the server host and is not a remote business operation",
    "profile": "all callers use the deployment's shared Lark profile",
    "update": "use scripts/update-cli.sh on the server after reviewing the reported update",
}
BLOCKED_FILE_FLAGS = {
    "-o", "--output", "--output-dir", "--file", "--local-dir", "--from-clipboard",
}
BLOCKED_APPS_COMMANDS = {"+init", "+env-pull", "+git-credential-init", "+git-credential-list", "+git-credential-remove"}
SAFE_BARE_FLAGS = {"--help", "-h", "--version", "-v"}
_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)$")


class _OriginCompatibleGitHubProvider(GitHubProvider):
    """Support Claude Code's published CIMD client when metadata fetches fail."""

    async def get_client(self, client_id: str):
        if client_id != CLAUDE_CODE_CLIENT_ID:
            return await super().get_client(client_id)
        client = await self._client_store.get(key=client_id)
        if client is not None:
            client.allowed_redirect_uri_patterns = [CLAUDE_CODE_REDIRECT_URI_PATTERN]
            await self._client_store.put(key=client_id, value=client)
            return client
        client = ProxyDCRClient(
            client_id=CLAUDE_CODE_CLIENT_ID,
            client_secret=None,
            redirect_uris=None,
            grant_types=["authorization_code"],
            response_types=["code"],
            scope="read:user",
            token_endpoint_auth_method="none",
            client_name="Claude Code",
            allowed_redirect_uri_patterns=[CLAUDE_CODE_REDIRECT_URI_PATTERN],
        )
        await self._client_store.put(key=client_id, value=client)
        return client


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _github_users() -> frozenset[str]:
    users = frozenset(user.strip().casefold() for user in _required(GITHUB_USERS_ENV).split(",") if user.strip())
    if not users:
        raise RuntimeError(f"{GITHUB_USERS_ENV} must contain at least one GitHub login")
    return users


def _base_url() -> str:
    base_url = _required(BASE_URL_ENV).rstrip("/")
    parsed = urlparse(base_url)
    hostname = parsed.hostname or ""
    valid_hostname = re.fullmatch(r"lark\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", hostname)
    if (
        parsed.scheme != "https" or not valid_hostname or parsed.username or parsed.password or parsed.port
        or parsed.path or parsed.params or parsed.query or parsed.fragment
    ):
        raise RuntimeError(f"{BASE_URL_ENV} must be an HTTPS origin using lark.<base-domain> without a port or path")
    return base_url


def _auth_provider() -> GitHubProvider:
    try:
        fernet = Fernet(_required(STORAGE_KEY_ENV).encode())
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"{STORAGE_KEY_ENV} must be a valid Fernet key") from error
    redis = RedisStore(host=os.environ.get(REDIS_HOST_ENV, "redis"), password=_required(REDIS_PASSWORD_ENV))
    return _OriginCompatibleGitHubProvider(
        client_id=_required(GITHUB_CLIENT_ID_ENV),
        client_secret=_required(GITHUB_CLIENT_SECRET_ENV),
        jwt_signing_key=_required(JWT_SIGNING_KEY_ENV),
        client_storage=FernetEncryptionWrapper(key_value=redis, fernet=fernet),
        base_url=_base_url(),
        resource_base_url=_base_url(),
        required_scopes=["read:user"],
        allowed_client_redirect_uris=ALLOWED_CLIENT_REDIRECT_URIS,
    )


def _authorized_github_user(ctx: AuthContext) -> bool:
    login = ((ctx.token.claims or {}).get("login") if ctx.token else "") or ""
    return login.casefold() in _github_users()


AUTH_PROVIDER = _auth_provider()
TOOL_META = {"securitySchemes": [{"type": "oauth2", "scopes": ["read:user"]}]}
mcp = FastMCP(
    name="Lark-CLI",
    version="0.1.0",
    instructions=(
        "Use lark_cli for Lark business commands and lark_cli_skill to browse the embedded, "
        "version-matched skill system. Arguments are an array after the lark-cli binary. "
        "Never add --yes until the user explicitly confirms a confirmation_required response."
    ),
    website_url=_base_url(),
    auth=AUTH_PROVIDER,
    middleware=[AuthMiddleware(auth=_authorized_github_user)],
)


def _flag_present(args: list[str], flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in args)


def _flag_value(args: list[str], flag: str) -> str | None:
    for index, arg in enumerate(args):
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
    return None


def _duration_seconds(value: str) -> float:
    match = _DURATION_RE.fullmatch(value)
    if not match:
        raise ValueError("event consume --timeout must use a duration such as 30s or 2m")
    amount, unit = int(match.group(1)), match.group(2)
    factors = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return amount * factors[unit]


def _validate_args(args: list[str], stdin: str | None) -> None:
    if not args:
        raise ValueError("args must contain a lark-cli command or a safe bare flag")
    if len(args) > MAX_ARGS:
        raise ValueError(f"args may contain at most {MAX_ARGS} values")
    if stdin is not None and len(stdin.encode()) > MAX_STDIN_BYTES:
        raise ValueError(f"stdin exceeds {MAX_STDIN_BYTES} bytes")
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("every argument must be a string")
        if len(arg.encode()) > MAX_ARG_BYTES:
            raise ValueError(f"each argument may contain at most {MAX_ARG_BYTES} bytes")
        if arg.startswith("@") and arg != "@-":
            raise ValueError("server-local @files are unavailable; use inline JSON or stdin with @-")
        for flag in BLOCKED_FILE_FLAGS | {"--profile"}:
            if arg == flag or arg.startswith(f"{flag}="):
                raise ValueError(f"{flag} is unavailable through this remote MCP")

    command = next((arg for arg in args if not arg.startswith("-")), None)
    if command is None:
        if not all(arg in SAFE_BARE_FLAGS for arg in args):
            raise ValueError(f"without a command, args may contain only {sorted(SAFE_BARE_FLAGS)}")
        return
    if command in BLOCKED_COMMANDS:
        raise ValueError(f"'{command}' is unavailable: {BLOCKED_COMMANDS[command]}")

    command_index = args.index(command)
    subcommand = next((arg for arg in args[command_index + 1:] if not arg.startswith("-")), None)
    if command == "apps" and subcommand in BLOCKED_APPS_COMMANDS:
        raise ValueError(f"'{command} {subcommand}' is unavailable because it changes server-local state")
    if command == "event":
        if subcommand in {"status", "stop"}:
            raise ValueError(f"'event {subcommand}' manages a server-local daemon and is unavailable")
        if subcommand == "consume":
            timeout = _flag_value(args, "--timeout")
            if timeout is None:
                raise ValueError("event consume requires --timeout no greater than 150s")
            seconds = _duration_seconds(timeout)
            if seconds <= 0 or seconds > MAX_EVENT_TIMEOUT_SECONDS:
                raise ValueError("event consume --timeout must be greater than zero and no greater than 150s")


def _validate_skill_path(path: str | None, required: bool = False) -> str | None:
    if path is None or not path.strip():
        if required:
            raise ValueError("path is required when action='read'")
        return None
    value = path.strip().replace("\\", "/")
    if "//" in value:
        raise ValueError("path must not contain empty segments")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("path must be a relative skill path without '.', '..', or empty segments")
    return str(parsed)


def _cli_path() -> str:
    configured = os.environ.get(CLI_ENV, "lark-cli")
    path = shutil.which(configured) if not Path(configured).is_file() else configured
    if not path:
        raise RuntimeError(f"lark-cli is not installed or not on PATH ({CLI_ENV}={configured})")
    return path


def _child_env(workdir: str) -> dict[str, str]:
    state_dir = os.environ.get(STATE_DIR_ENV, "/var/lib/lark-cli")
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": state_dir,
        "XDG_CONFIG_HOME": f"{state_dir}/config",
        "XDG_DATA_HOME": f"{state_dir}/data",
        "TMPDIR": workdir,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
        "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
    }


def _execute(args: list[str], stdin: str | None = None, timeout: float | None = None) -> dict:
    with tempfile.TemporaryDirectory(dir="/tmp") as workdir:
        try:
            result = subprocess.run(
                [_cli_path(), *args], input=stdin, text=True, capture_output=True,
                timeout=timeout or TIMEOUT_SECONDS, cwd=workdir, env=_child_env(workdir), check=False,
            )
        except subprocess.TimeoutExpired as error:
            return {
                "exit_code": None,
                "stdout": error.stdout or "",
                "stderr": error.stderr or "",
                "timed_out": True,
                "hint": f"lark-cli timed out after {timeout or TIMEOUT_SECONDS:g}s; narrow the request and retry once",
            }
    response = {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": False}
    for key in ("stdout", "stderr"):
        encoded = response[key].encode()
        if len(encoded) > MAX_OUTPUT_BYTES:
            response[key] = encoded[:MAX_OUTPUT_BYTES].decode(errors="ignore")
            response["truncated"] = True
            response["original_bytes"] = len(encoded)
            response["hint"] = "CLI output exceeded 10 MiB; narrow the command or request fewer results"
    return response


_VERSION_LOCK = threading.Lock()
_INSTALLED_VERSION: str | None = None
_LATEST_STATE = {"version": None, "checked_at": 0.0, "checking": False}
_UPDATE_INTERVAL_SECONDS = 6 * 60 * 60
_LATEST_RELEASE_URL = "https://api.github.com/repos/larksuite/cli/releases/latest"


def _parse_version(text: str) -> tuple[int, ...] | None:
    cleaned = text.strip().lstrip("v")
    if not re.fullmatch(r"\d+(?:\.\d+)+", cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def _installed_version() -> str | None:
    global _INSTALLED_VERSION
    if _INSTALLED_VERSION is not None:
        return _INSTALLED_VERSION
    configured = os.environ.get("LARK_CLI_MCP_INSTALLED_VERSION", "")
    if _parse_version(configured):
        _INSTALLED_VERSION = configured
        return _INSTALLED_VERSION
    result = _execute(["--version"], timeout=10)
    if result["exit_code"] == 0:
        match = re.search(r"\d+(?:\.\d+)+", result["stdout"] or result["stderr"])
        _INSTALLED_VERSION = match.group(0) if match else None
    return _INSTALLED_VERSION


def _refresh_latest_version() -> None:
    try:
        request = urllib.request.Request(
            _LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "lark-cli-mcp"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed HTTPS URL
            payload = json.loads(response.read())
        latest = str(payload.get("tag_name", "")).lstrip("v")
        if not payload.get("prerelease") and _parse_version(latest):
            with _VERSION_LOCK:
                _LATEST_STATE["version"] = latest
    except Exception as error:  # noqa: BLE001 - update checks never affect tool calls
        LOGGER.debug("lark-cli update check failed: %s", error)
    finally:
        with _VERSION_LOCK:
            _LATEST_STATE["checked_at"] = time.time()
            _LATEST_STATE["checking"] = False


def _maybe_start_update_check() -> None:
    if os.environ.get(UPDATE_CHECK_ENV, "1") == "0":
        return
    with _VERSION_LOCK:
        stale = time.time() - float(_LATEST_STATE["checked_at"]) > _UPDATE_INTERVAL_SECONDS
        if not stale or _LATEST_STATE["checking"]:
            return
        _LATEST_STATE["checking"] = True
    threading.Thread(target=_refresh_latest_version, daemon=True).start()


def _update_available() -> dict[str, str] | None:
    _maybe_start_update_check()
    current = _installed_version()
    with _VERSION_LOCK:
        latest = _LATEST_STATE["version"]
    if not current or not latest or not _parse_version(current) or not _parse_version(str(latest)):
        return None
    if _parse_version(str(latest)) <= _parse_version(current):
        return None
    return {
        "current_version": current,
        "latest_version": str(latest),
        "upgrade_command": f"sudo scripts/update-cli.sh {latest}",
    }


def _attach_update(response: dict) -> dict:
    update = _update_available()
    if update:
        response["update_available"] = update
    return response


@mcp.tool(
    title="Run Lark CLI",
    meta=TOOL_META,
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
def lark_cli(args: list[str], stdin: str | None = None) -> dict:
    """Run a supported lark-cli command and return raw stdout, stderr, and exit metadata.

    Pass arguments after the `lark-cli` binary as an array. Server authentication, profiles,
    updates, local files, clipboard access, and unbounded event consumers are unavailable.
    Exit code 10 with `confirmation_required` must be shown to the user; append `--yes` only after
    explicit confirmation. `update_available`, when present, is informational for the administrator.
    """
    _validate_args(args, stdin)
    return _attach_update(_execute(args, stdin))


@mcp.tool(
    title="Browse Lark CLI skills",
    meta=TOOL_META,
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
def lark_cli_skill(action: str = "list", path: str | None = None) -> dict:
    """List or read version-matched skills embedded in lark-cli.

    Use action `list` without a path for the catalog, `list` with a skill/directory path for one
    layer, and `read` with a file path for SKILL.md or a reference file.
    """
    if action not in {"list", "read"}:
        raise ValueError("action must be 'list' or 'read'")
    clean_path = _validate_skill_path(path, required=action == "read")
    args = ["skills", action]
    if clean_path:
        args.append(clean_path)
    args.append("--json")
    response = _execute(args)
    if response["exit_code"] == 0:
        try:
            response["result"] = json.loads(response["stdout"])
        except json.JSONDecodeError:
            response["result"] = response["stdout"]
    return _attach_update(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
