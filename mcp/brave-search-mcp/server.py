#!/usr/bin/env python3
"""OAuth-protected, search-only wrapper for the bx Brave Search CLI."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

BASE_URL_ENV = "BRAVE_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "BRAVE_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "BRAVE_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "BRAVE_MCP_GITHUB_USERS"
JWT_SIGNING_KEY_ENV = "BRAVE_MCP_JWT_SIGNING_KEY"
STORAGE_KEY_ENV = "BRAVE_MCP_STORAGE_KEY"
REDIS_PASSWORD_ENV = "BRAVE_MCP_REDIS_PASSWORD"
REDIS_HOST_ENV = "BRAVE_MCP_REDIS_HOST"
CLI_ENV = "BRAVE_MCP_BX_PATH"

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
MAX_ARGS = 128
MAX_ARG_BYTES = 16 * 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 180
BLOCKED_FLAGS = ("--api-key", "--base-url", "--config")
GLOBAL_VALUE_FLAGS = {"--timeout", "--extra", "--endpoint"}


class _OriginCompatibleGitHubProvider(GitHubProvider):
    """Handle Claude Code's published CIMD client when metadata retrieval fails."""

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
    users = frozenset(
        user.strip().casefold()
        for user in _required(GITHUB_USERS_ENV).split(",")
        if user.strip()
    )
    if not users:
        raise RuntimeError(f"{GITHUB_USERS_ENV} must contain at least one GitHub login")
    return users


def _base_url() -> str:
    base_url = _required(BASE_URL_ENV).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        raise RuntimeError(f"{BASE_URL_ENV} must be an HTTPS origin without a path")
    return base_url


def _auth_provider() -> GitHubProvider:
    storage_key = _required(STORAGE_KEY_ENV).encode()
    try:
        fernet = Fernet(storage_key)
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"{STORAGE_KEY_ENV} must be a valid Fernet key") from error
    redis = RedisStore(
        host=os.environ.get(REDIS_HOST_ENV, "redis"),
        password=_required(REDIS_PASSWORD_ENV),
    )
    return _OriginCompatibleGitHubProvider(
        client_id=_required(GITHUB_CLIENT_ID_ENV),
        client_secret=_required(GITHUB_CLIENT_SECRET_ENV),
        jwt_signing_key=_required(JWT_SIGNING_KEY_ENV),
        client_storage=FernetEncryptionWrapper(key_value=redis, fernet=fernet),
        base_url=_base_url(),
        resource_base_url=_base_url(),
        required_scopes=["read:user"],
        allowed_client_redirect_uris=[
            "https://chatgpt.com/connector/oauth/*",
            "https://chatgpt.com/connector_platform_oauth_redirect",
            "https://claude.ai/api/mcp/auth_callback",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    )


def _authorized_github_user(ctx: AuthContext) -> bool:
    login = ((ctx.token.claims or {}).get("login") if ctx.token else "") or ""
    return login.casefold() in _github_users()


AUTH_PROVIDER = _auth_provider()
TOOL_META = {"securitySchemes": [{"type": "oauth2", "scopes": ["read:user"]}]}
mcp = FastMCP(
    name="Brave-Search-CLI",
    version="0.1.0",
    instructions=(
        "Use brave_search_cli to run bx search commands. Pass arguments without the bx binary. "
        "当前部署套餐（2026-07-22 实测）仅开放 web/news/images/videos 四个 search 命令，默认用 web；"
        "context/answers/places/suggest/spellcheck/config 均不可用，调用会返回 OPTION_NOT_IN_PLAN 或被拒绝。"
        "本地 --goggles @文件 与 --api-key/--config/--base-url 也不可用。详见工具描述。"
    ),
    website_url=_base_url(),
    auth=AUTH_PROVIDER,
    middleware=[AuthMiddleware(auth=_authorized_github_user)],
)


def _is_blocked_flag(arg: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for flag in BLOCKED_FLAGS)


def _validate_args(args: list[str], stdin: str | None) -> None:
    if len(args) > MAX_ARGS:
        raise ValueError(f"args may contain at most {MAX_ARGS} values")
    if stdin is not None and len(stdin.encode()) > MAX_STDIN_BYTES:
        raise ValueError(f"stdin exceeds {MAX_STDIN_BYTES} bytes")
    positional_seen = False
    skip_next = False
    previous = ""
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("every argument must be a string")
        if len(arg.encode()) > MAX_ARG_BYTES:
            raise ValueError(f"each argument may contain at most {MAX_ARG_BYTES} bytes")
        if _is_blocked_flag(arg):
            raise ValueError(f"{arg.split('=', 1)[0]} is managed by the server and unavailable")
        if arg.startswith("--goggles=@") and arg != "--goggles=@-":
            raise ValueError("local @file Goggles are unavailable; use @- or an HTTPS URL")
        if skip_next:
            if previous == "--goggles" and arg.startswith("@") and arg != "@-":
                raise ValueError("local @file Goggles are unavailable; use @- or an HTTPS URL")
            skip_next = False
            previous = arg
            continue
        if arg in GLOBAL_VALUE_FLAGS or arg == "--goggles":
            skip_next = True
            previous = arg
            continue
        if arg.startswith("-") or positional_seen:
            previous = arg
            continue
        positional_seen = True
        if arg == "config":
            raise ValueError("bx config is unavailable through this MCP")
        previous = arg
    if skip_next:
        raise ValueError("a flag requiring a value is missing its value")


def _bx_path() -> str:
    configured = os.environ.get(CLI_ENV, "bx")
    path = shutil.which(configured) if not Path(configured).is_file() else configured
    if not path:
        raise RuntimeError(f"bx is not installed or not on PATH ({CLI_ENV}={configured})")
    return path


def _child_env() -> dict[str, str]:
    return {
        "BRAVE_SEARCH_API_KEY": _required("BRAVE_SEARCH_API_KEY"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


@mcp.tool(
    title="Run Brave Search CLI",
    meta=TOOL_META,
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def brave_search_cli(args: list[str], stdin: str | None = None) -> dict:
    """Pass args and optional stdin to bx; returns its unmodified stdout, stderr, and exit code.

    `args` 是 bx 后的参数数组（不含 "bx" 本身，不用 shell 字符串）。仅需 stdin 时才传，例如 `--goggles @-`。

    当前部署套餐仅开放四个 search 命令，默认用 web：

    | 目标 | args 示例 |
    |---|---|
    | 文档、错误、代码模式 | ["web", "Python asyncio gather vs wait", "--count", "5"] |
    | 传统网页结果 | ["web", "site:docs.rs axum middleware", "--count", "5"] |
    | 论坛讨论 | ["web", "Rust async runtime", "--result-filter", "discussions"] |
    | 时效新闻 | ["news", "npm security advisory", "--freshness", "pd"] |
    | 图片、视频 | ["images", "microservice diagram"] / ["videos", "Rust async tutorial"] |

    不要调用 context/answers/places/suggest/spellcheck/config：均返回 OPTION_NOT_IN_PLAN 或直接被本
    MCP 拒绝，重试无用。web 用较小的 --count（如 5）控制 token；用 --include-site/--exclude-site 或内联
    --goggles 控制来源。--api-key/--config/--base-url 和本地 --goggles @文件 不可用（服务端持有凭据）。

    退出码：0 成功；timed_out=true 缩小查询后重试一次；1/2 修正参数；3 服务端凭据或套餐问题，报告 stderr
    不要改 API Key；4 限流，退避重试；5 网络或 Brave 端错误，退避重试一次。
    """
    _validate_args(args, stdin)
    try:
        result = subprocess.run(
            [_bx_path(), *args],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            env=_child_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "exit_code": None,
            "stdout": error.stdout or "",
            "stderr": error.stderr or "",
            "timed_out": True,
        }
    if len(result.stdout.encode()) > MAX_OUTPUT_BYTES or len(result.stderr.encode()) > MAX_OUTPUT_BYTES:
        raise RuntimeError(f"bx output exceeds {MAX_OUTPUT_BYTES} bytes")
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
