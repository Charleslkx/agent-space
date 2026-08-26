#!/usr/bin/env python3
"""OAuth-protected, search-only wrapper for the bx Brave Search CLI."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import threading
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
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Abrave-search/oauth/callback"
GROK_REDIRECT_URI = "https://grok.com/connectors-oauth-exchange-code/"
ALLOWED_CLIENT_REDIRECT_URIS = [
    "https://chatgpt.com/connector/oauth/*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    GROK_REDIRECT_URI,
    WORKBUDDY_REDIRECT_URI,
    "http://localhost:*",
    "http://127.0.0.1:*",
]
MAX_ARGS = 128
MAX_ARG_BYTES = 16 * 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 180
BLOCKED_FLAGS = ("--api-key", "--base-url", "--config")

# Only these reach bx. The rest of its command set is either unavailable on this
# deployment's plan (context/answers/places/suggest/spellcheck) or manages the
# server's own credentials (config). Rejecting locally saves the round trip and,
# for `config`, is the security boundary.
ALLOWED_COMMANDS = {"web", "news", "images", "videos"}
NO_COMMAND_FLAGS = {"--help", "-h", "--version", "-V"}

# ponytail: caps concurrent bx processes, not requests -- the anyio worker pool
# still admits 40 callers, they just queue here. Raise only together with the
# container's mem_limit/pids_limit.
MAX_CONCURRENT_CALLS = max(1, int(os.environ.get("BRAVE_MCP_MAX_CONCURRENCY", "16")))
ACQUIRE_TIMEOUT_SECONDS = 5
_CALL_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)


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
        allowed_client_redirect_uris=ALLOWED_CLIENT_REDIRECT_URIS,
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
        "Use brave_search_cli to run bx search commands. Pass arguments without the bx binary, "
        "and put the subcommand first (args[0]); a bare query is rejected. "
        "当前部署套餐（2026-07-22 实测）仅开放 web/news/images/videos 四个 search 命令，默认用 web；"
        "context/answers/places/suggest/spellcheck/config 均不可用，调用会被本 MCP 直接拒绝。"
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
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("every argument must be a string")
        if len(arg.encode()) > MAX_ARG_BYTES:
            raise ValueError(f"each argument may contain at most {MAX_ARG_BYTES} bytes")
        if _is_blocked_flag(arg):
            raise ValueError(f"{arg.split('=', 1)[0]} is managed by the server and unavailable")
        if arg.startswith("--goggles=@") and arg != "--goggles=@-":
            raise ValueError("local @file Goggles are unavailable; use @- or an HTTPS URL")
    # Reject an @file anywhere after a bare --goggles. This over-rejects when
    # --goggles is itself some other flag's value, which clap refuses anyway.
    for previous, arg in zip(args, args[1:]):
        if previous == "--goggles" and arg.startswith("@") and arg != "@-":
            raise ValueError("local @file Goggles are unavailable; use @- or an HTTPS URL")

    if args and all(arg in NO_COMMAND_FLAGS for arg in args):
        return
    # The command must be args[0]. Inferring it by scanning for the first
    # non-flag token means this validator and clap can disagree about which
    # token is the command, which is exactly how an allowlist gets bypassed.
    if not args or args[0] not in ALLOWED_COMMANDS:
        found = args[0] if args else "nothing"
        raise ValueError(
            f"args must start with one of {sorted(ALLOWED_COMMANDS)}, or be exactly one of "
            f"{sorted(NO_COMMAND_FLAGS)}; got {found!r}. context/answers/places/suggest/"
            "spellcheck are not in this deployment's plan and config is server-managed. "
            "A bare [\"query\"] is rejected too: bx would route it to context."
        )


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

    `args` 是 bx 后的参数数组（不含 "bx" 本身，不用 shell 字符串）。**args[0] 必须是子命令**，
    不能前置任何标志，也不能只传查询词（bx 会把裸查询路由到不可用的 context）。仅需 stdin 时才传
    `stdin`，例如 `--goggles @-`。

    当前部署套餐仅开放四个 search 命令，默认用 web：

    | 目标 | args 示例 |
    |---|---|
    | 文档、错误、代码模式 | ["web", "Python asyncio gather vs wait", "--count", "5"] |
    | 传统网页结果 | ["web", "site:docs.rs axum middleware", "--count", "5"] |
    | 论坛讨论 | ["web", "Rust async runtime", "--result-filter", "discussions"] |
    | 时效新闻 | ["news", "npm security advisory", "--freshness", "pd"] |
    | 图片、视频 | ["images", "microservice diagram"] / ["videos", "Rust async tutorial"] |

    context/answers/places/suggest/spellcheck/config 会被本 MCP 直接拒绝（前五个不在套餐内，config 由
    服务端持有凭据），重试无用。web 用较小的 --count（如 5）控制 token；用 --include-site/--exclude-site
    或内联 --goggles 控制来源。--api-key/--config/--base-url 和本地 --goggles @文件 不可用。

    响应字段：总是有 `exit_code`、`stdout`、`stderr`（原样）、`timed_out`；输出超 10MiB 时另有
    `truncated`、`original_bytes` 和 `hint`（内容是截断不是丢弃，缩小 --count 后重试）。

    退出码：0 成功；timed_out=true 缩小查询后重试一次；1/2 修正参数；3 服务端凭据或套餐问题，报告 stderr
    不要改 API Key；4 限流，退避重试；5 网络或 Brave 端错误，退避重试一次。若报 "server is at capacity"，
    说明本次没有执行任何请求，等几秒重试即可。
    """
    _validate_args(args, stdin)
    if not _CALL_SLOTS.acquire(timeout=ACQUIRE_TIMEOUT_SECONDS):
        raise RuntimeError(
            f"server is at capacity ({MAX_CONCURRENT_CALLS} concurrent bx calls); "
            "nothing ran, retry in a few seconds"
        )
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
    except OSError as error:
        raise RuntimeError(f"could not run bx: {error}") from error
    finally:
        _CALL_SLOTS.release()
    response: dict = {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": False,
    }
    for key in ("stdout", "stderr"):
        text = response[key]
        # 4 bytes is UTF-8's per-character ceiling, so anything shorter than a
        # quarter of the cap cannot be oversized -- skip the encode, which is a
        # full second copy of the output and is paid on every single call.
        if len(text) <= MAX_OUTPUT_BYTES // 4:
            continue
        encoded = text.encode()
        if len(encoded) > MAX_OUTPUT_BYTES:
            response[key] = encoded[:MAX_OUTPUT_BYTES].decode(errors="ignore")
            response["truncated"] = True
            response["original_bytes"] = len(encoded)
            response["hint"] = (
                f"Output exceeded {MAX_OUTPUT_BYTES} bytes and was truncated (not discarded). "
                "Narrow it with a smaller --count or with --result-filter."
            )
    return response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Fail at boot rather than on every call: otherwise a missing key or a
    # missing bx leaves the container healthy (the healthcheck only opens a
    # socket) while every tool call fails.
    _required("BRAVE_SEARCH_API_KEY")
    _bx_path()
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
