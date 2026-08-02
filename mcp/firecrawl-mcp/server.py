#!/usr/bin/env python3
"""OAuth-protected, read-focused wrapper for the firecrawl CLI (firecrawl/cli)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
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

LOGGER = logging.getLogger("firecrawl_mcp")

BASE_URL_ENV = "FIRECRAWL_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "FIRECRAWL_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "FIRECRAWL_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "FIRECRAWL_MCP_GITHUB_USERS"
JWT_SIGNING_KEY_ENV = "FIRECRAWL_MCP_JWT_SIGNING_KEY"
STORAGE_KEY_ENV = "FIRECRAWL_MCP_STORAGE_KEY"
REDIS_PASSWORD_ENV = "FIRECRAWL_MCP_REDIS_PASSWORD"
REDIS_HOST_ENV = "FIRECRAWL_MCP_REDIS_HOST"
CLI_ENV = "FIRECRAWL_MCP_CLI_PATH"
UPDATE_CHECK_ENV = "FIRECRAWL_MCP_UPDATE_CHECK"

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Afirecrawl/oauth/callback"
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

# ponytail: caps concurrent firecrawl processes, not requests -- the anyio
# worker pool still admits 40 callers, they just queue here. Kept well under the
# container's mem_limit/pids_limit; raise both together or not at all.
MAX_CONCURRENT_CALLS = max(1, int(os.environ.get("FIRECRAWL_MCP_MAX_CONCURRENCY", "8")))
ACQUIRE_TIMEOUT_SECONDS = 5

# Subcommands that reach the CLI. Everything else either manages local
# credentials/config, writes to local disk, opens a persistent remote
# browser session, or builds standing infrastructure (scheduled jobs,
# webhooks) on Firecrawl's side -- all out of scope for a shared,
# credential-holding server. Bare `--help`/`--version`/`--status` (no
# subcommand) are allowed separately below.
ALLOWED_COMMANDS = {"search", "scrape", "map", "crawl", "agent", "research", "credit-usage"}
NO_COMMAND_FLAGS = {"--help", "-h", "--version", "-V", "--status"}

BLOCKED_COMMAND_REASONS = {
    "monitor": "builds a persistent scheduled job with webhook/email notifications; not exposed here. "
    "Poll with scrape/crawl from the client side instead.",
    "feedback": "sends usage feedback data back to Firecrawl; not exposed here.",
    "search-feedback": "sends usage feedback data back to Firecrawl; not exposed here.",
    "browser": "opens a persistent remote browser session on the shared server; not exposed here.",
    "launch": "opens a persistent remote browser session on the shared server; not exposed here.",
    "interact": "drives a live browser session (clicks/forms/login) on third-party sites; not exposed here. "
    "Use scrape to read content instead.",
    "download": "writes files to the server's local disk, which this MCP does not expose. "
    "Use crawl and read the response's stdout field instead.",
    "experimental": "wraps `download`, which writes to local disk; not exposed here.",
    "x": "alias for `experimental`/`download`, which writes to local disk; not exposed here.",
    "parse": "only accepts a local file path on the server's disk, which this MCP does not expose. "
    "Use scrape with the URL of the file instead.",
    "config": "manages stored CLI credentials; the server already holds the API key and this is unavailable.",
    "view-config": "manages stored CLI credentials; not exposed here.",
    "login": "manages stored CLI credentials; the server already holds the API key and this is unavailable.",
    "logout": "manages stored CLI credentials; not exposed here.",
    "init": "installs skills/config on a local machine; not applicable to this remote server.",
    "setup": "installs skills/config on a local machine; not applicable to this remote server.",
    "make": "changes local default-agent configuration; not applicable to this remote server.",
    "env": "pulls a project's local .env file; not applicable to this remote server.",
    "doctor": "diagnoses the local install/environment; not applicable to this remote server.",
    "version": "use `[\"--version\"]` instead; the installed and latest versions are also reported "
    "via the `update_available` field when a newer release exists.",
}

# Flags that would leak the server's credentials, write to local disk, or
# exfiltrate via webhook -- rejected in both `--flag value` and
# `--flag=value` form, wherever they appear in `args`.
BLOCKED_VALUE_FLAGS = {
    "-k": "the server holds the Firecrawl API key; a per-call key is not accepted.",
    "--api-key": "the server holds the Firecrawl API key; a per-call key is not accepted.",
    "--api-url": "the server's API endpoint is fixed and cannot be overridden.",
    "-o": "results are returned via this response's `stdout` field, not written to a file.",
    "--output": "results are returned via this response's `stdout` field, not written to a file.",
    "--schema-file": "local files are not available; pass the schema inline instead: --schema '{...}'.",
    "--actions-file": "local files are not available; pass actions inline instead: --actions '[...]'.",
    "--scrape-options-file": "local files are not available; pass options inline instead: --scrape-options '{...}'.",
    "--webhook": "outbound webhooks are not available on this server.",
    "--profile": "a persistent browser profile would leave shared login state on the server; not available.",
}
BLOCKED_BOOLEAN_FLAGS = {
    "--no-save-changes": "persistent browser profiles are not available on this server.",
}


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
    name="Firecrawl-CLI",
    version="0.1.0",
    instructions=(
        "Use firecrawl_cli to run search/scrape/map/crawl/agent/research/credit-usage through the "
        "Firecrawl CLI; other subcommands are unavailable (see tool description). The subcommand "
        "must be args[0] -- flags may not precede it. "
        "Use firecrawl_skill(name) for deeper reference docs when the compact tool description isn't enough."
    ),
    website_url=_base_url(),
    auth=AUTH_PROVIDER,
    middleware=[AuthMiddleware(auth=_authorized_github_user)],
)


def _is_blocked_value_flag(arg: str) -> str | None:
    for flag, reason in BLOCKED_VALUE_FLAGS.items():
        if arg == flag or arg.startswith(f"{flag}="):
            return reason
    return None


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

        blocked_reason = _is_blocked_value_flag(arg)
        if blocked_reason is not None:
            flag_name = arg.split("=", 1)[0]
            raise ValueError(f"{flag_name} is unavailable through this MCP: {blocked_reason}")
        if arg in BLOCKED_BOOLEAN_FLAGS:
            raise ValueError(f"{arg} is unavailable through this MCP: {BLOCKED_BOOLEAN_FLAGS[arg]}")

    if args and all(arg in NO_COMMAND_FLAGS for arg in args):
        return

    # The command must be args[0]. Scanning for the first non-flag token instead
    # would let this validator and the CLI's own parser disagree about which one
    # it is -- any root-level option taking a value would swallow the token this
    # sees as the command, and the CLI would run the *next* one.
    command = args[0] if args else None
    if command in BLOCKED_COMMAND_REASONS:
        raise ValueError(f"'{command}' is unavailable through this MCP: {BLOCKED_COMMAND_REASONS[command]}")
    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"args[0] must be the subcommand, but got {command!r}. Allowed: {sorted(ALLOWED_COMMANDS)}, "
            f"or exactly one of {sorted(NO_COMMAND_FLAGS)}. Flags may not precede the subcommand -- "
            "move them after it, e.g. [\"search\", \"query\", \"--limit\", \"5\"]. "
            "See the tool description for the full list of blocked commands and why."
        )


def _cli_path() -> str:
    configured = os.environ.get(CLI_ENV, "firecrawl")
    path = shutil.which(configured) if not Path(configured).is_file() else configured
    if not path:
        raise RuntimeError(f"firecrawl is not installed or not on PATH ({CLI_ENV}={configured})")
    return path


def _child_env(workdir: str) -> dict[str, str]:
    return {
        "FIRECRAWL_API_KEY": _required("FIRECRAWL_API_KEY"),
        "FIRECRAWL_NO_UPDATE_CHECK": "1",
        "FIRECRAWL_NO_SEARCH_FEEDBACK": "1",
        "FIRECRAWL_NO_ENDPOINT_FEEDBACK": "1",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


# ---------------------------------------------------------------------------
# Background, best-effort CLI version check. Never blocks or fails a tool
# call: any network/parse error is swallowed and simply retried later.
# ---------------------------------------------------------------------------
_CALL_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)
_VERSION_LOCK = threading.Lock()
_PROBE_LOCK = threading.Lock()
_UNPROBED = "<unprobed>"
# _UNPROBED vs None matters: None means "probed, could not tell", and caching it
# is what stops the probe from re-running on every single tool call.
_INSTALLED_VERSION: str | None = _UNPROBED
_LATEST_STATE = {"version": None, "checked_at": 0.0, "checking": False}
_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
_LATEST_RELEASE_URL = "https://api.github.com/repos/firecrawl/cli/releases/latest"


def _installed_version() -> str | None:
    # NB: plain `firecrawl --version` (unlike `--version --auth-status`) still runs the CLI's
    # own update-notice check before Commander prints the version and exits, so this must go
    # through the same disposable-HOME + FIRECRAWL_NO_UPDATE_CHECK path as a real tool call.
    global _INSTALLED_VERSION
    if _INSTALLED_VERSION is not _UNPROBED:
        return _INSTALLED_VERSION
    # ponytail: the lock serialises a cold start's concurrent callers onto one
    # probe instead of one each. It is held across a subprocess, which is fine
    # because it runs at most once per process.
    with _PROBE_LOCK:
        if _INSTALLED_VERSION is not _UNPROBED:
            return _INSTALLED_VERSION
        try:
            with tempfile.TemporaryDirectory(dir="/tmp") as workdir:
                result = subprocess.run(
                    [_cli_path(), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=workdir,
                    env=_child_env(workdir),
                    check=False,
                )
            version = result.stdout.strip() or result.stderr.strip()
            _INSTALLED_VERSION = version or None
        except Exception:  # noqa: BLE001 - version probing must never break the server
            _INSTALLED_VERSION = None
        return _INSTALLED_VERSION


def _parse_version(text: str) -> tuple[int, ...] | None:
    cleaned = text.strip().lstrip("v")
    parts = cleaned.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _refresh_latest_version() -> None:
    try:
        request = urllib.request.Request(
            _LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "firecrawl-mcp"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed https URL
            payload = json.loads(response.read())
        tag = str(payload.get("tag_name", "")).lstrip("v")
        if tag:
            with _VERSION_LOCK:
                _LATEST_STATE["version"] = tag
    except Exception as error:  # noqa: BLE001 - background check must never raise
        LOGGER.debug("firecrawl update check failed: %s", error)
    finally:
        with _VERSION_LOCK:
            _LATEST_STATE["checked_at"] = time.time()
            _LATEST_STATE["checking"] = False


def _maybe_start_update_check() -> None:
    with _VERSION_LOCK:
        stale = (time.time() - _LATEST_STATE["checked_at"]) > _UPDATE_CHECK_INTERVAL_SECONDS
        if not stale or _LATEST_STATE["checking"]:
            return
        _LATEST_STATE["checking"] = True
    try:
        threading.Thread(target=_refresh_latest_version, daemon=True).start()
    except RuntimeError:
        # Could not spawn the thread; clear the flag so a later call retries
        # instead of leaving the check wedged as "in progress" forever.
        with _VERSION_LOCK:
            _LATEST_STATE["checking"] = False


def _update_available() -> str | None:
    if os.environ.get(UPDATE_CHECK_ENV, "1") == "0":
        return None
    _maybe_start_update_check()
    with _VERSION_LOCK:
        latest = _LATEST_STATE["version"]
    # Probe the local CLI only when there is something to compare it against.
    # The probe is a subprocess on the critical path of every tool call.
    if not latest:
        return None
    installed = _installed_version()
    if not installed:
        return None
    installed_parsed = _parse_version(installed)
    latest_parsed = _parse_version(latest)
    if installed_parsed is None or latest_parsed is None:
        return None
    if latest_parsed > installed_parsed:
        return latest
    return None


@mcp.tool(
    title="Run Firecrawl CLI",
    meta=TOOL_META,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def firecrawl_cli(args: list[str], stdin: str | None = None) -> dict:
    """Pass args to the firecrawl CLI; returns its stdout, stderr, and exit code unmodified.

    `args` is the argument array after "firecrawl" (not a shell string, don't include "firecrawl"
    itself). **args[0] must be the subcommand** -- flags may not precede it. Pass URLs as a single
    element, unquoted. `stdin` is rarely needed by these commands.

    ## Allowed commands, key flags, and a ready-to-use example

    | command | positional | key flags |
    |---|---|---|
    | search | `<query>` | `--limit` (default 5, max 100), `--sources web,images,news`, `--categories github,research,pdf`, `--tbs qdr:h\\|d\\|w\\|m`, `--location`, `--country`, `--scrape`, `--scrape-formats`, `--json` |
    | scrape | `[urls...]` (one or more) | `-f/--format` (comma-separated, e.g. markdown,links), `--only-main-content`, `--wait-for <ms>`, `--include-tags`/`--exclude-tags`, `-Q/--query "<question>"`, `--schema '<inline json>'`, `--actions '<inline json>'`, `--max-age`, `--country`, `--languages`, `--json --pretty` |
    | map | `<url>` | `--search "<query>"` (filter URLs within the site), `--limit`, `--sitemap only\\|include\\|skip`, `--include-subdomains`, `--json` |
    | crawl | `<url>` or `<jobId>` | `--limit`, `--max-depth`, `--include-paths`/`--exclude-paths`, `--crawl-entire-domain`, `--allow-subdomains`, `--max-concurrency`, `--scrape-options '<inline json>'`, `--status` (check a job), `--cancel` |
    | agent | `<prompt>` | `--urls`, `--model spark-1-mini\\|spark-1-pro`, `--schema '<inline json>'`, `--max-credits`, `--status`, `--cancel` |
    | research | `search-papers <q>` \\| `inspect-paper <id>` \\| `related-papers <id>` \\| `read-paper <id>` \\| `search-github <q>` | see `firecrawl_skill("overview")` |
    | credit-usage | (none) | `--json --pretty` |

    Example: `["search", "site:docs.rs axum middleware", "--limit", "5"]`
    Example: `["scrape", "https://example.com/pricing", "--format", "markdown"]`
    Example: `["crawl", "https://example.com/docs", "--limit", "20"]` (async, see below)

    ## Async jobs (crawl / agent)

    `crawl` and `agent` return a job id immediately unless `--wait` is passed. This tool times out
    after 180s, so do NOT pass `--wait` on a large crawl. Instead: call once without `--wait` to get
    the job id, then poll with `["crawl", "<jobId>", "--status"]` (or `["agent", "<jobId>", "--status"]`).

    ## Unavailable commands and flags (with the alternative)

    Blocked commands: monitor (builds persistent scheduled jobs/webhooks — poll from the client
    instead), feedback/search-feedback (sends usage data), browser/launch/interact (persistent
    remote browser sessions), download/experimental/x (writes to local disk — use crawl),
    parse (needs a local file — scrape the file's URL instead), config/view-config/login/logout
    (server holds the API key), init/setup/make/env/doctor (local-machine only).

    Blocked flags (server-managed or local-file related): `-k/--api-key`, `--api-url` (server holds
    credentials), `-o/--output` (read the `stdout` field of this response instead), `--schema-file`/
    `--actions-file`/`--scrape-options-file` (pass inline JSON instead: `--schema '{...}'`),
    `--webhook` (no outbound webhooks), `--profile`/`--no-save-changes` (no persistent browser state).

    ## Response fields

    Always: `exit_code` (0 or 1 — the CLI only uses these two), `stdout`, `stderr` (unmodified),
    `timed_out`. May also include: `truncated` + `original_bytes` when output exceeded 10MiB (output
    is NOT discarded, just cut off — narrow with `--limit` or a single `--format` and retry);
    `hint` on truncation or timeout with a concrete next step; `update_available` with the latest
    version string when a newer firecrawl CLI release exists (mention it to the user, do not try to
    upgrade yourself). A "server is at capacity" error means nothing ran -- retry in a few seconds.

    ## Reading stderr

    The CLI has no exit-code taxonomy beyond 0/1 — the actionable detail is always in the stderr
    text, and wording varies by endpoint (observed: `Request failed with status code 401`,
    `Unauthorized: Invalid token`, `Error: ...`). Look for a 3-digit HTTP code first; if there
    isn't one, match on words: "unauthorized"/"invalid token"/"forbidden" -> bad key or missing
    permission, report it, don't retry; "credit"/402 -> credits exhausted, stop retrying;
    "rate limit"/429 -> back off and retry once; 5xx or a network/timeout message -> back off and
    retry once; anything else is almost always a bad argument -- fix it per the message and retry.

    For deeper usage (complex `--actions` sequences, schema design, crawl path strategy), call
    `firecrawl_skill(name)`.
    """
    _validate_args(args, stdin)
    update_available = _update_available()

    if not _CALL_SLOTS.acquire(timeout=ACQUIRE_TIMEOUT_SECONDS):
        raise RuntimeError(
            f"server is at capacity ({MAX_CONCURRENT_CALLS} concurrent firecrawl calls); "
            "nothing ran, retry in a few seconds"
        )
    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as workdir:
            try:
                result = subprocess.run(
                    [_cli_path(), *args],
                    input=stdin,
                    text=True,
                    capture_output=True,
                    timeout=TIMEOUT_SECONDS,
                    cwd=workdir,
                    env=_child_env(workdir),
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                response = {
                    "exit_code": None,
                    "stdout": error.stdout or "",
                    "stderr": error.stderr or "",
                    "timed_out": True,
                    "hint": (
                        "Timed out after "
                        f"{TIMEOUT_SECONDS}s. For crawl/agent, drop --wait and poll with "
                        "[\"crawl\", \"<jobId>\", \"--status\"] instead."
                    ),
                }
                if update_available:
                    response["update_available"] = update_available
                return response
            except OSError as error:
                raise RuntimeError(f"could not run firecrawl: {error}") from error
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
                "Output exceeded 10MiB and was truncated (not discarded). Narrow the result with "
                "--limit, a single --format, or by paging a crawl through its job id."
            )
    if update_available:
        response["update_available"] = update_available
    return response


_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_SKILL_NAMES = ("overview", "search", "scrape", "map", "crawl", "agent")


@mcp.tool(
    title="Firecrawl CLI reference",
    meta=TOOL_META,
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def firecrawl_skill(name: str = "overview") -> str:
    """Return deeper reference documentation for one firecrawl command family.

    Optional: the firecrawl_cli description already covers common flags and a ready-to-use example
    for every allowed command. Call this only when you need more depth (complex --actions
    sequences, schema design, crawl path strategy, or the research subcommands).

    `name` must be one of: overview, search, scrape, map, crawl, agent.
    """
    if name not in _SKILL_NAMES:
        raise ValueError(f"unknown skill '{name}'; choose one of {_SKILL_NAMES}")
    return (_SKILLS_DIR / f"{name}.md").read_text()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Fail at boot rather than on every call: otherwise a missing key or a
    # missing CLI leaves the container healthy (the healthcheck only opens a
    # socket) while every tool call fails.
    _required("FIRECRAWL_API_KEY")
    _cli_path()
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
