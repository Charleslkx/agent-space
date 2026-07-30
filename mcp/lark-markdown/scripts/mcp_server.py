#!/usr/bin/env python3
"""Small FastMCP wrapper around lark-cli document and whiteboard operations."""
from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import ipaddress
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.middleware import AuthMiddleware

TOKEN_ENV = "LARK_MCP_AUTH_TOKEN"
TOKEN_FILE_ENV = "LARK_MCP_AUTH_TOKEN_FILE"
AUTH_MODE_ENV = "LARK_MCP_AUTH_MODE"
BASE_URL_ENV = "LARK_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "LARK_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "LARK_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "LARK_MCP_GITHUB_USERS"
GITHUB_USER_ENV = "LARK_MCP_GITHUB_USER"  # Backward-compatible single-user setting.
GITHUB_JWT_SIGNING_KEY_ENV = "LARK_MCP_JWT_SIGNING_KEY"
CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Alark-markdown/oauth/callback"
ALLOWED_CLIENT_REDIRECT_URIS = [
    "https://chatgpt.com/connector/oauth/*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    WORKBUDDY_REDIRECT_URI,
    "http://localhost:*",
    "http://127.0.0.1:*",
]
CLI_TIMEOUT_SECONDS = 60
MAX_BATCH_ITEMS = 100
DEFAULT_BATCH_CONCURRENCY = 4
MAX_BATCH_CONCURRENCY = 8
MAX_CONTENT_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_SNIPPET_CONTEXT_CHARS = 1000
MAX_SNIPPET_MATCHES = 10
MAX_SEARCH_RESULTS = 20
SEARCH_HIGHLIGHT_OPEN = re.compile(r'<h>')
SEARCH_HIGHLIGHT_CLOSE = re.compile(r'</h>')
XML_ATTRIBUTE = re.compile(r'([:\w-]+)\s*=\s*(["\'])(.*?)\2', re.DOTALL)
XML_IMAGE = re.compile(r'<img\b(?P<attrs>[^>]*)/?>', re.IGNORECASE | re.DOTALL)
XML_WHITEBOARD = re.compile(
    r'<whiteboard\b(?P<attrs>[^>]*)>(?P<source>.*?)</whiteboard\s*>',
    re.IGNORECASE | re.DOTALL,
)
MARKDOWN_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
DISPLAY_FORMULA = re.compile(r"(?ms)^[ \t]*\$\$[ \t]*(?:\n)?(.*?)(?:\n)?[ \t]*\$\$[ \t]*$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESTART_CONFIRMATION = "RESTART_LARK_MARKDOWN_MCP"
RESTART_WORKER = PROJECT_ROOT / "scripts" / "restart_mcp_worker.py"

DocFormat = Literal["markdown", "xml"]


def _center_display_math(content: str) -> str:
    """Convert standalone Markdown display formulas, excluding fenced code blocks."""
    def convert(prose: str) -> str:
        def replace(match: re.Match[str]) -> str:
            formula = html.escape(match.group(1).strip(), quote=False)
            return f'<p align="center"><latex>{formula}</latex></p>'

        return DISPLAY_FORMULA.sub(replace, prose)

    output: list[str] = []
    prose: list[str] = []
    closing: re.Pattern[str] | None = None
    for line in content.splitlines(keepends=True):
        if closing:
            output.append(line)
            if closing.match(line):
                closing = None
            continue
        match = MARKDOWN_FENCE.match(line)
        if match:
            output.append(convert("".join(prose)))
            prose.clear()
            marker = match.group(1)
            closing = re.compile(rf"^[ ]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
            output.append(line)
        else:
            prose.append(line)
    output.append(convert("".join(prose)))
    return "".join(output)


def _auth_mode() -> str:
    mode = os.environ.get(AUTH_MODE_ENV)
    if mode:
        if mode not in {"none", "token", "github"}:
            raise RuntimeError(f"{AUTH_MODE_ENV} must be none, token, or github")
        return mode
    return "token" if os.environ.get(TOKEN_ENV) or os.environ.get(TOKEN_FILE_ENV) else "none"


AUTH_MODE = _auth_mode()


def _static_token() -> str:
    token = os.environ.get(TOKEN_ENV)
    token_file = os.environ.get(TOKEN_FILE_ENV)
    if token and token_file:
        raise RuntimeError(f"set only one of {TOKEN_ENV} and {TOKEN_FILE_ENV}")
    if token_file:
        path = Path(token_file)
        try:
            stat = path.stat()
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"{TOKEN_FILE_ENV} must point to a regular file")
            if stat.st_uid != os.geteuid():
                raise RuntimeError(f"{TOKEN_FILE_ENV} must be owned by the service user")
            if stat.st_mode & 0o077:
                raise RuntimeError(f"{TOKEN_FILE_ENV} permissions must be 0600 or stricter")
            token = path.read_text().strip()
        except OSError as error:
            raise RuntimeError(f"cannot read {TOKEN_FILE_ENV}: {error}") from error
    if not token:
        raise RuntimeError(f"token auth requires {TOKEN_ENV} or {TOKEN_FILE_ENV}")
    if len(token) < 32:
        raise RuntimeError("static auth token must contain at least 32 characters")
    return token


class _OriginCompatibleGitHubProvider(GitHubProvider):
    """Handle narrowly scoped compatibility differences in MCP OAuth clients."""

    async def get_client(self, client_id: str):
        """Provide Claude Code's public CIMD client when its metadata fetch is blocked.

        Claude Code identifies itself with a public client-metadata URL. FastMCP
        normally fetches that document, but Claude's edge may reject server-side
        requests. The fallback is deliberately limited to the published client
        ID and its documented loopback callback; all other clients keep FastMCP's
        normal DCR/CIMD validation path.
        """
        if client_id != CLAUDE_CODE_CLIENT_ID:
            return await super().get_client(client_id)

        client = await self._client_store.get(key=client_id)
        if client is not None:
            if client.allowed_redirect_uri_patterns != [CLAUDE_CODE_REDIRECT_URI_PATTERN]:
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

    async def authorize(self, client, params):
        client_resource = getattr(params, "resource", None)
        if client_resource:
            requested = urlparse(str(client_resource))
            configured = urlparse(str(self.base_url))
            if (
                requested.scheme == configured.scheme
                and requested.netloc == configured.netloc
                and requested.path.rstrip("/") == ""
            ):
                params = params.model_copy(
                    update={"resource": f"{str(self.base_url).rstrip('/')}/mcp"}
                )
        return await super().authorize(client, params)


def _auth_provider(mode: str = AUTH_MODE):
    if mode == "github":
        names = (
            BASE_URL_ENV, GITHUB_CLIENT_ID_ENV,
            GITHUB_CLIENT_SECRET_ENV, GITHUB_JWT_SIGNING_KEY_ENV,
        )
        missing = [name for name in names if not os.environ.get(name)]
        if not os.environ.get(GITHUB_USERS_ENV) and not os.environ.get(GITHUB_USER_ENV):
            missing.append(f"{GITHUB_USERS_ENV} (or legacy {GITHUB_USER_ENV})")
        if missing:
            raise RuntimeError(f"GitHub OAuth requires: {', '.join(missing)}")
        base_url = os.environ[BASE_URL_ENV].rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path:
            raise RuntimeError(f"{BASE_URL_ENV} must be an HTTPS origin without a path")
        return _OriginCompatibleGitHubProvider(
            client_id=os.environ[GITHUB_CLIENT_ID_ENV],
            client_secret=os.environ[GITHUB_CLIENT_SECRET_ENV],
            jwt_signing_key=os.environ[GITHUB_JWT_SIGNING_KEY_ENV],
            base_url=base_url,
            resource_base_url=base_url,
            required_scopes=["read:user"],
            allowed_client_redirect_uris=ALLOWED_CLIENT_REDIRECT_URIS,
        )
    if mode == "none":
        return None
    token = _static_token()
    from fastmcp.server.auth import StaticTokenVerifier

    return StaticTokenVerifier(tokens={token: {
        "client_id": "lark-markdown",
        "scopes": ["lark:read", "lark:write"],
    }})


def _github_users() -> frozenset[str]:
    users_value = os.environ.get(GITHUB_USERS_ENV, "").strip()
    legacy_user = os.environ.get(GITHUB_USER_ENV, "").strip()
    if users_value and legacy_user:
        raise RuntimeError(f"set only one of {GITHUB_USERS_ENV} and {GITHUB_USER_ENV}")
    users = frozenset(user.strip().casefold() for user in (users_value or legacy_user).split(",") if user.strip())
    if not users:
        raise RuntimeError(f"set {GITHUB_USERS_ENV} to at least one GitHub login")
    return users


def _authorized_github_user(ctx: AuthContext) -> bool:
    login = ((ctx.token.claims or {}).get("login") if ctx.token else "") or ""
    return login.casefold() in _github_users()


def _security_schemes(mode: str) -> list[dict]:
    if mode == "github":
        return [{"type": "oauth2", "scopes": ["read:user"]}]
    if mode == "none":
        return [{"type": "noauth"}]
    return []


TOOL_META = {"securitySchemes": _security_schemes(AUTH_MODE)}
AUTH_PROVIDER = _auth_provider()
mcp = FastMCP(
    name="Lark-Markdown",
    version="0.14.0",
    instructions=(
        "For normal document work, use the connected tools and never configure or start a server. "
        "Call check_lark_cli only when connection or user auth is uncertain; use begin_lark_auth "
        "and complete_lark_auth only to recover missing user authorization. When the target document "
        "is unknown, call search_documents first to rank candidates by keyword relevance across docs, "
        "wiki, and sheets, then call find_document_text on the chosen doc; never guess a doc token. "
        "Before a local edit, use "
        "find_document_text to return bounded snippets; if it finds multiple targets, refine the query with "
        "longer exact text and never guess. Use point_update or batch_point_update only for exact targets; "
        "they reject non-unique patterns. Use batch_pull only when full-document understanding is requested, the target cannot be "
        "narrowed by snippets, or XML/native-block structure is needed. Use batch_push only for an explicit "
        "whole-document replacement or append. After point_update, verify with find_document_text using the "
        "replacement (or the removed text for deletion); use batch_pull only after partial_success, an error, "
        "a format-sensitive operation, or an explicit verification request. Use scan_document_assets to list "
        "images and whiteboards in a Lark document without returning its full XML."
    ),
    website_url=os.environ.get(BASE_URL_ENV),
    auth=AUTH_PROVIDER,
    middleware=(
        [AuthMiddleware(auth=_authorized_github_user)]
        if AUTH_MODE == "github" else []
    ),
)
WORKDIR = PROJECT_ROOT / ".lark_publish"
QUIET_ENV = {
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}
# A verified Lark session is persisted by lark-cli. Rechecking it for every
# document call causes unnecessary refresh attempts without making writes safer.
AUTH_CACHE_SECONDS = 15 * 60
_auth_cache: tuple[float, dict] | None = None


class LarkCLIError(RuntimeError):
    def __init__(self, details: dict):
        self.details = details
        super().__init__()

    def __str__(self) -> str:
        return json.dumps(self.details, ensure_ascii=False)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _https_config(host: str, cert: Path | None, key: Path | None) -> dict[str, str]:
    if bool(cert) != bool(key):
        raise RuntimeError("--tls-cert and --tls-key must be supplied together")
    if not _is_loopback(host) and AUTH_PROVIDER is None:
        raise RuntimeError("public HTTP binding requires configured authentication")
    if not _is_loopback(host) and not cert:
        raise RuntimeError("public HTTP binding requires --tls-cert and --tls-key")
    if cert:
        if AUTH_PROVIDER is None:
            raise RuntimeError("HTTPS mode requires configured authentication")
        if not cert.is_file() or not key or not key.is_file():
            raise RuntimeError("TLS certificate or key file does not exist")
        return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    return {}


def _run_process(
    args: list[str], operation: str, suppress_update: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **QUIET_ENV}
    if suppress_update:
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    try:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            env=env,
            cwd=PROJECT_ROOT,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise LarkCLIError({
            "operation": operation,
            "error": "timeout",
            "timeout_seconds": CLI_TIMEOUT_SECONDS,
            "next_step": "retry once; if it repeats, run lark-cli auth status --json --verify",
        }) from error
    if result.returncode:
        raise LarkCLIError({
            "operation": operation,
            "error": "lark_cli_failed",
            "exit_code": result.returncode,
            "message": (result.stderr.strip() or result.stdout.strip())[:4000],
        })
    return result


def _run_cli(args: list[str], context: str = "lark-cli") -> dict:
    result = _run_process(["lark-cli", *args], context)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LarkCLIError({
            "operation": context,
            "error": "invalid_json",
            "message": str(error),
        }) from error


def _check_lark_cli(use_cache: bool = True) -> dict:
    global _auth_cache
    if use_cache and _auth_cache and time.monotonic() < _auth_cache[0]:
        return _auth_cache[1]
    executable = shutil.which("lark-cli")
    if not executable:
        raise LarkCLIError({
            "operation": "check lark-cli",
            "error": "not_installed",
            "message": "lark-cli is not installed or not on PATH",
        })
    version = None
    version_warning = None
    try:
        version = _run_process(
            [executable, "--version"], "check lark-cli version", suppress_update=True,
        ).stdout.strip()
    except LarkCLIError as error:
        version_warning = error.details
    for attempt in range(2):
        auth = _run_cli(
            ["auth", "status", "--json", "--verify"],
            "check lark-cli user authentication",
        )
        user = auth.get("identities", {}).get("user", {})
        if auth.get("verified") and user.get("verified") and user.get("available"):
            break
        if attempt == 0:
            time.sleep(1)
    else:
        raise LarkCLIError({
            "operation": "check lark-cli user authentication",
            "error": "authentication_unavailable",
            "message": "verified user authentication is unavailable after one retry",
            "next_step": "call begin_lark_auth",
        })
    status = {
        "executable": executable,
        "version": version,
        "identity": auth.get("identity"),
        "user_status": user.get("status"),
        "verified": True,
    }
    update_notice = auth.get("_notice", {}).get("update")
    if update_notice:
        status["update_notice"] = update_notice
    if version_warning:
        status["version_warning"] = version_warning
    _auth_cache = (time.monotonic() + AUTH_CACHE_SECONDS, status)
    return status


@contextmanager
def _hidden_run() -> Iterator[Path]:
    if WORKDIR.is_symlink():
        raise RuntimeError("refusing to use a symlinked .lark_publish directory")
    WORKDIR.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=".run-", dir=WORKDIR))
    original_error: BaseException | None = None
    try:
        yield path
    except BaseException as error:
        original_error = error
        raise
    finally:
        try:
            shutil.rmtree(path)
        except OSError as error:
            message = f"failed to remove temporary payload {path.name}: {error}"
            if isinstance(original_error, LarkCLIError):
                original_error.details["cleanup_error"] = message
            elif original_error is not None:
                raise LarkCLIError({
                    "operation": "temporary payload cleanup",
                    "error": "operation_and_cleanup_failed",
                    "cause": {
                        "error": type(original_error).__name__,
                        "message": str(original_error),
                    },
                    "cleanup_error": message,
                }) from original_error
            else:
                raise RuntimeError(message) from error
        if WORKDIR.exists() and not any(WORKDIR.iterdir()):
            WORKDIR.rmdir()


def _cleanup_stale_runs() -> None:
    if WORKDIR.is_symlink():
        raise RuntimeError("refusing to clean a symlinked .lark_publish directory")
    if not WORKDIR.exists():
        return
    for path in WORKDIR.glob(".run-*"):
        shutil.rmtree(path)
    if not any(WORKDIR.iterdir()):
        WORKDIR.rmdir()


def _payload(path: Path, content: str) -> str:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
    path.write_text(content, encoding="utf-8")
    return "@./" + path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _validate_batch(items: list, name: str) -> None:
    if not items:
        raise ValueError(f"{name} must contain at least one item")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"{name} exceeds {MAX_BATCH_ITEMS} items")


def _batch_workers(concurrency: int, item_count: int) -> int:
    if isinstance(concurrency, bool) or not isinstance(concurrency, int):
        raise ValueError("concurrency must be an integer")
    if not 1 <= concurrency <= MAX_BATCH_CONCURRENCY:
        raise ValueError(f"concurrency must be between 1 and {MAX_BATCH_CONCURRENCY}")
    return min(concurrency, item_count)


def _batch_failure(operation: str, index: int, item: str, completed: int, error: Exception) -> LarkCLIError:
    details = error.details if isinstance(error, LarkCLIError) else {
        "error": type(error).__name__, "message": str(error),
    }
    return LarkCLIError({
        "operation": operation,
        "failed_index": index,
        "failed_item": item,
        "completed": completed,
        "cause": details,
    })


def _revision_id(document: dict, operation: str) -> int:
    value = document.get("revision_id")
    try:
        revision_id = int(value)
    except (TypeError, ValueError) as error:
        raise LarkCLIError({
            "operation": operation,
            "error": "invalid_response",
            "message": "document revision_id must be an integer",
        }) from error
    if revision_id < 0:
        raise LarkCLIError({
            "operation": operation,
            "error": "invalid_response",
            "message": "document revision_id must not be negative",
        })
    return revision_id


def _is_revision_conflict(error: Exception) -> bool:
    details = error.details if isinstance(error, LarkCLIError) else str(error)
    message = json.dumps(details, ensure_ascii=False).casefold()
    return "1770021" in message or "too old document" in message


def _find_auth_value(payload: object, names: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _find_auth_value(value, names)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _find_auth_value(value, names)
            if found:
                return found
    return None


def _lark_auth_qrcode(verification_url: str) -> str:
    with _hidden_run() as run:
        output = run / "lark-auth.png"
        _run_process([
                "lark-cli", "auth", "qrcode", verification_url,
                "--output", "./" + output.resolve().relative_to(PROJECT_ROOT).as_posix(),
            ], "generate lark authorization QR code")
        try:
            return base64.b64encode(output.read_bytes()).decode("ascii")
        except OSError as error:
            raise LarkCLIError({
                "operation": "generate lark authorization QR code",
                "error": "missing_output",
                "message": str(error),
            }) from error


@mcp.tool(title="Start Lark user authorization", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def begin_lark_auth() -> dict:
    """Create a one-time Lark device authorization URL and QR code for Docs, Drive, and Wiki."""
    payload = _run_cli(
        ["auth", "login", "--domain", "docs", "--domain", "drive", "--domain", "wiki", "--no-wait", "--json"],
        "start lark user authorization",
    )
    verification_url = _find_auth_value(
        payload, ("verification_url", "verification_uri_complete"),
    )
    device_code = _find_auth_value(payload, ("device_code",))
    if not verification_url or not device_code:
        raise LarkCLIError({
            "operation": "start lark user authorization",
            "error": "invalid_response",
            "message": "lark-cli did not return a verification URL and device code",
        })
    return {
        "verification_url": verification_url,
        "device_code": device_code,
        "qr_code_png_base64": _lark_auth_qrcode(verification_url),
        "next_step": "Open the verification URL or scan the QR code, complete authorization, then call complete_lark_auth with the device code.",
    }


@mcp.tool(title="Complete Lark user authorization", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def complete_lark_auth(device_code: str) -> dict:
    """Complete a Lark device authorization after the user has approved its URL."""
    global _auth_cache
    if not device_code.strip():
        raise ValueError("device_code must not be empty")
    _run_cli(
        ["auth", "login", "--device-code", device_code],
        "complete lark user authorization",
    )
    _auth_cache = None
    return _check_lark_cli(use_cache=False)


def _fetch_document(
    doc: str, doc_format: DocFormat, detail: Literal["simple", "with-ids", "full"], operation: str,
) -> dict:
    result = _run_cli([
        "docs", "+fetch", "--api-version", "v2", "--as", "user",
        "--doc", doc, "--doc-format", doc_format, "--detail", detail,
        "--format", "json",
    ], operation)
    try:
        return result["data"]["document"]
    except (KeyError, TypeError) as error:
        raise LarkCLIError({
            "operation": operation,
            "error": "invalid_response",
            "message": "missing data.document",
        }) from error


def _xml_attributes(fragment: str) -> dict[str, str]:
    return {name: value for name, _, value in XML_ATTRIBUTE.findall(fragment)}


def _scan_xml_assets(content: str) -> tuple[list[dict], list[dict]]:
    images = []
    for match in XML_IMAGE.finditer(content):
        attributes = _xml_attributes(match.group("attrs"))
        images.append({
            "offset": match.start(),
            "source": attributes.get("src") or attributes.get("url"),
            "attributes": attributes,
        })
    whiteboards = []
    for match in XML_WHITEBOARD.finditer(content):
        attributes = _xml_attributes(match.group("attrs"))
        whiteboards.append({
            "offset": match.start(),
            "token": next((attributes.get(name) for name in (
                "block-token", "block_token", "block-id", "block_id", "token",
            ) if attributes.get(name)), None),
            "format": attributes.get("type"),
            "attributes": attributes,
        })
    return images, whiteboards


@mcp.tool(title="Check Lark CLI", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def check_lark_cli() -> dict:
    """Check lark-cli and user login; return any optional update notice without blocking."""
    return _check_lark_cli(use_cache=False)


@mcp.tool(title="Batch pull Lark documents", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def batch_pull(
    documents: list[str],
    doc_format: DocFormat = "markdown",
    detail: Literal["simple", "with-ids", "full"] = "simple",
    concurrency: int = DEFAULT_BATCH_CONCURRENCY,
) -> list[dict]:
    """Fetch documents concurrently; results remain in input order."""
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if detail not in {"simple", "with-ids", "full"}:
        raise ValueError("detail must be simple, with-ids, or full")
    _validate_batch(documents, "documents")
    workers = _batch_workers(concurrency, len(documents))
    for index, doc in enumerate(documents):
        if not isinstance(doc, str) or not doc.strip():
            raise _batch_failure(
                "batch_pull", index, "", 0, ValueError("document must not be empty"),
            )
    def pull_one(index: int, doc: str) -> dict:
        try:
            document = _fetch_document(
                doc, doc_format, detail, f"batch_pull documents[{index}]",
            )
        except RuntimeError as error:
            raise _batch_failure("batch_pull", index, doc, 0, error) from error
        return {
            "doc": doc,
            "revision_id": document.get("revision_id"),
            "content": document.get("content", ""),
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(pull_one, index, doc) for index, doc in enumerate(documents)]
        pulled = []
        for index, (doc, future) in enumerate(zip(documents, futures)):
            try:
                pulled.append(future.result())
            except RuntimeError as error:
                failure = _batch_failure("batch_pull", index, doc, len(pulled), error)
                failure.details["concurrency"] = workers
                failure.details["other_requests_may_have_completed"] = workers > 1
                raise failure from error
    return pulled


@mcp.tool(title="Find document text snippets", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def find_document_text(
    doc: str,
    query: str,
    context_chars: int = 240,
    max_matches: int = 3,
    doc_format: DocFormat = "markdown",
) -> dict:
    """Return bounded context around exact text matches without returning the full document."""
    if not doc.strip():
        raise ValueError("doc must not be empty")
    if not query:
        raise ValueError("query must not be empty")
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if type(context_chars) is not int or not 0 <= context_chars <= MAX_SNIPPET_CONTEXT_CHARS:
        raise ValueError(f"context_chars must be an integer between 0 and {MAX_SNIPPET_CONTEXT_CHARS}")
    if type(max_matches) is not int or not 1 <= max_matches <= MAX_SNIPPET_MATCHES:
        raise ValueError(f"max_matches must be an integer between 1 and {MAX_SNIPPET_MATCHES}")
    _check_lark_cli()
    document = _fetch_document(doc, doc_format, "simple", "find_document_text")
    content = document.get("content", "")
    if not isinstance(content, str):
        raise LarkCLIError({
            "operation": "find_document_text",
            "error": "invalid_response",
            "message": "document content must be a string",
        })
    starts = []
    offset = 0
    while len(starts) < max_matches:
        start = content.find(query, offset)
        if start < 0:
            break
        starts.append(start)
        offset = start + len(query)
    total_matches = content.count(query)
    return {
        "doc": doc,
        "revision_id": document.get("revision_id"),
        "query": query,
        "match_count": total_matches,
        "matches_truncated": total_matches > len(starts),
        "matches": [
            {
                "start": start,
                "end": start + len(query),
                "before": content[max(0, start - context_chars):start],
                "match": query,
                "after": content[start + len(query):start + len(query) + context_chars],
            }
            for start in starts
        ],
    }


def _clean_highlight(value: str | None) -> str:
    if not value:
        return ""
    return SEARCH_HIGHLIGHT_CLOSE.sub("**", SEARCH_HIGHLIGHT_OPEN.sub("**", value))


@mcp.tool(title="Search Lark docs, wiki, and sheets", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def search_documents(
    query: str,
    doc_types: str | None = None,
    count: int = 10,
) -> list[dict]:
    """Keyword-rank docs/wiki/sheets by relevance (Search v2) and return candidates with highlighted snippets.

    Use to find which document to target before find_document_text or batch_pull;
    matching is server-side keyword ranking, not semantic/embedding search.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if type(count) is not int or not 1 <= count <= MAX_SEARCH_RESULTS:
        raise ValueError(f"count must be an integer between 1 and {MAX_SEARCH_RESULTS}")
    _check_lark_cli()
    args = [
        "drive", "+search", "--as", "user", "--query", query,
        "--page-size", str(count), "--format", "json",
    ]
    if doc_types:
        args.extend(["--doc-types", doc_types])
    payload = _run_cli(args, "search_documents")
    results = payload.get("data", {}).get("results")
    if not isinstance(results, list):
        raise LarkCLIError({
            "operation": "search_documents",
            "error": "invalid_response",
            "message": "missing data.results",
        })
    candidates = []
    for result in results:
        meta = result.get("result_meta") or {}
        candidates.append({
            "doc": meta.get("token"),
            "url": meta.get("url"),
            "entity_type": result.get("entity_type"),
            "title": _clean_highlight(result.get("title_highlighted")),
            "snippet": _clean_highlight(result.get("summary_highlighted")),
            "owner_name": meta.get("owner_name"),
            "update_time_iso": meta.get("update_time_iso"),
        })
    return candidates


@mcp.tool(title="Batch push Lark documents", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def batch_push(
    documents: list[dict[str, str]], concurrency: int = DEFAULT_BATCH_CONCURRENCY,
) -> list[dict]:
    """Write independent documents concurrently; results remain in input order."""
    _validate_batch(documents, "documents")
    workers = _batch_workers(concurrency, len(documents))
    prepared = []
    for index, item in enumerate(documents):
        if not isinstance(item, dict):
            raise _batch_failure("batch_push", index, "", 0, ValueError("item must be an object"))
        doc_value = item.get("doc")
        content = item.get("content")
        mode = item.get("mode", "overwrite")
        doc_format = item.get("doc_format", "markdown")
        if not isinstance(doc_value, str) or not doc_value.strip():
            raise _batch_failure("batch_push", index, "", 0, ValueError("doc must not be empty"))
        doc = doc_value.strip()
        if not isinstance(content, str):
            raise _batch_failure("batch_push", index, doc, 0, ValueError("content must be a string"))
        if mode not in {"overwrite", "append"}:
            raise _batch_failure("batch_push", index, doc, 0, ValueError("mode must be overwrite or append"))
        if doc_format not in {"markdown", "xml"}:
            raise _batch_failure("batch_push", index, doc, 0, ValueError("doc_format must be markdown or xml"))
        if doc_format == "markdown":
            content = _center_display_math(content)
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise _batch_failure(
                "batch_push", index, doc, 0,
                ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes"),
            )
        prepared.append((doc, content, mode, doc_format))
    _check_lark_cli()
    with _hidden_run() as run:
        payloads = [
            _payload(run / f".{index}.content", raw_content)
            for index, (_, raw_content, _, _) in enumerate(prepared)
        ]

        def push_one(index: int) -> dict:
            doc, _, mode, doc_format = prepared[index]
            try:
                result = _run_cli([
                    "docs", "+update", "--api-version", "v2", "--as", "user",
                    "--doc", doc, "--command", mode,
                    "--doc-format", doc_format, "--content", payloads[index], "--format", "json",
                ], f"batch_push documents[{index}]")
            except RuntimeError as error:
                raise _batch_failure("batch_push", index, doc, 0, error) from error
            return {"doc": doc, "result": result}

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(push_one, index) for index in range(len(prepared))]
            results = []
            for index, ((doc, _, _, _), future) in enumerate(zip(prepared, futures)):
                try:
                    results.append(future.result())
                except RuntimeError as error:
                    failure = _batch_failure("batch_push", index, doc, len(results), error)
                    failure.details["concurrency"] = workers
                    failure.details["other_requests_may_have_completed"] = workers > 1
                    raise failure from error
    return results


def _write_exact_text(
    doc: str,
    pattern: str,
    replacement: str,
    doc_format: DocFormat,
    revision_id: int,
) -> dict:
    with _hidden_run() as run:
        content = _payload(
            run / ".replacement",
            _center_display_math(replacement) if doc_format == "markdown" else replacement,
        )
        return _run_cli([
            "docs", "+update", "--api-version", "v2", "--as", "user",
            "--doc", doc, "--command", "str_replace", "--pattern", pattern,
            "--revision-id", str(revision_id),
            "--doc-format", doc_format, "--content", content, "--format", "json",
        ], "point_update")


def _preflight_content(document: dict, operation: str) -> tuple[str, int]:
    content = document.get("content", "")
    if not isinstance(content, str):
        raise LarkCLIError({
            "operation": operation,
            "error": "invalid_response",
            "message": "document content must be a string",
        })
    return content, _revision_id(document, operation)


@mcp.tool(title="Update exact document text", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def point_update(
    doc: str,
    pattern: str,
    replacement: str,
    doc_format: DocFormat = "markdown",
) -> dict:
    """Replace an exact text target only when it occurs once; the full document stays server-side."""
    if not doc.strip():
        raise ValueError("doc must not be empty")
    if not pattern:
        raise ValueError("pattern must not be empty")
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    _check_lark_cli()
    document = _fetch_document(doc, doc_format, "simple", "point_update preflight")
    content, revision_id = _preflight_content(document, "point_update preflight")
    matches = content.count(pattern)
    if matches != 1:
        raise ValueError(
            f"pattern must occur exactly once, found {matches}; call find_document_text for bounded context"
        )
    return _write_exact_text(doc, pattern, replacement, doc_format, revision_id)


@mcp.tool(title="Update multiple exact document texts", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def batch_point_update(
    doc: str,
    updates: list[dict[str, str]],
    doc_format: DocFormat = "markdown",
    expected_revision_id: int | None = None,
) -> list[dict]:
    """Preflight and apply ordered exact replacements with revision guards."""
    if not doc.strip():
        raise ValueError("doc must not be empty")
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if expected_revision_id is not None and (
        isinstance(expected_revision_id, bool) or not isinstance(expected_revision_id, int)
        or expected_revision_id < 0
    ):
        raise ValueError("expected_revision_id must be a non-negative integer")
    _validate_batch(updates, "updates")
    prepared: list[tuple[str, str]] = []
    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            raise _batch_failure("batch_point_update", index, doc, 0, ValueError("item must be an object"))
        pattern = update.get("pattern")
        replacement = update.get("replacement")
        if not isinstance(pattern, str) or not pattern:
            raise _batch_failure("batch_point_update", index, doc, 0, ValueError("pattern must not be empty"))
        if not isinstance(replacement, str):
            raise _batch_failure("batch_point_update", index, doc, 0, ValueError("replacement must be a string"))
        prepared.append((pattern, replacement))
    _check_lark_cli()
    document = _fetch_document(doc, doc_format, "simple", "batch_point_update preflight")
    content, revision_id = _preflight_content(document, "batch_point_update preflight")
    if expected_revision_id is not None and revision_id != expected_revision_id:
        failure = _batch_failure(
            "batch_point_update", 0, doc, 0,
            ValueError(f"expected revision {expected_revision_id}, found {revision_id}"),
        )
        failure.details.update({
            "error": "revision_conflict",
            "expected_revision_id": expected_revision_id,
            "actual_revision_id": revision_id,
            "applied_indexes": [],
        })
        raise failure
    simulated = content
    for index, (pattern, replacement) in enumerate(prepared):
        matches = simulated.count(pattern)
        if matches != 1:
            failure = _batch_failure(
                "batch_point_update", index, doc, 0,
                ValueError(f"pattern must occur exactly once, found {matches}"),
            )
            failure.details.update({
                "error": "preflight_conflict",
                "expected_revision_id": revision_id,
                "applied_indexes": [],
            })
            raise failure
        simulated = simulated.replace(pattern, replacement, 1)
    results = []
    for index, (pattern, replacement) in enumerate(prepared):
        try:
            result = _write_exact_text(doc, pattern, replacement, doc_format, revision_id)
        except Exception as error:
            failure = _batch_failure("batch_point_update", index, doc, len(results), error)
            failure.details.update({
                "error": "revision_conflict" if _is_revision_conflict(error) else "remote_error",
                "expected_revision_id": revision_id,
                "applied_indexes": list(range(len(results))),
            })
            raise failure from error
        results.append({"pattern": pattern, "result": result})
        try:
            revision_id = _revision_id(result.get("data", {}).get("document", {}), "batch_point_update")
        except Exception as error:
            failure = _batch_failure("batch_point_update", index, doc, len(results), error)
            failure.details.update({
                "error": "remote_error",
                "expected_revision_id": revision_id,
                "applied_indexes": list(range(len(results))),
            })
            raise failure from error
    return results


@mcp.tool(title="Create a Lark document", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_document(
    content: str,
    doc_format: DocFormat = "markdown",
    parent_token: str | None = None,
) -> dict:
    """Create one Lark Docx in a Drive folder, Wiki node, or personal space."""
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    with _hidden_run() as run:
        payload = _payload(
            run / ".document",
            _center_display_math(content) if doc_format == "markdown" else content,
        )
        args = [
            "docs", "+create", "--api-version", "v2", "--as", "user",
            "--doc-format", doc_format, "--content", payload, "--format", "json",
        ]
        if parent_token:
            args.extend(["--parent-token", parent_token])
        return _run_cli(args, "create_document")


@mcp.tool(title="Create a Lark Wiki node", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_wiki_node(
    title: str,
    parent_node_token: str | None = None,
    space_id: str | None = None,
) -> dict:
    """Create a blank Docx Wiki node in a specified Wiki space or beneath a parent node."""
    if not title.strip():
        raise ValueError("title must not be empty")
    if not (parent_node_token or space_id):
        raise ValueError("parent_node_token or space_id is required")
    _check_lark_cli()
    args = [
        "wiki", "+node-create", "--as", "user", "--title", title,
        "--obj-type", "docx", "--format", "json",
    ]
    if parent_node_token:
        args.extend(["--parent-node-token", parent_node_token])
    if space_id:
        args.extend(["--space-id", space_id])
    return _run_cli(args, "create_wiki_node")


@mcp.tool(title="Create a Lark Wiki space", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_wiki_space(name: str, description: str | None = None) -> dict:
    """Create a Wiki space owned by the authorized Lark user."""
    if not name.strip():
        raise ValueError("name must not be empty")
    _check_lark_cli()
    args = ["wiki", "+space-create", "--as", "user", "--name", name, "--format", "json"]
    if description:
        args.extend(["--description", description])
    return _run_cli(args, "create_wiki_space")


@mcp.tool(title="Scan document images and whiteboards", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def scan_document_assets(doc: str) -> dict:
    """Return image and whiteboard metadata from one Lark document without exposing its full XML."""
    if not doc.strip():
        raise ValueError("doc must not be empty")
    _check_lark_cli()
    document = _fetch_document(doc, "xml", "full", "scan_document_assets")
    content = document.get("content", "")
    if not isinstance(content, str):
        raise LarkCLIError({
            "operation": "scan_document_assets",
            "error": "invalid_response",
            "message": "document content must be a string",
        })
    images, whiteboards = _scan_xml_assets(content)
    return {
        "doc": doc,
        "revision_id": document.get("revision_id"),
        "images": images,
        "whiteboards": whiteboards,
        "counts": {"images": len(images), "whiteboards": len(whiteboards)},
    }


@mcp.tool(title="Insert document media", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def insert_media(
    doc: str,
    filename: str,
    content_base64: str,
    media_type: str = "image",
    selection: str | None = None,
    before: bool = False,
) -> dict:
    """Insert a base64 image or file, then delete its hidden local payload."""
    _check_lark_cli()
    if media_type not in {"image", "file"}:
        raise ValueError("media_type must be image or file")
    if not filename or Path(filename).name != filename:
        raise ValueError("filename must be a plain file name without directories")
    if before and not selection:
        raise ValueError("before requires selection")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("content_base64 is not valid base64") from error
    if len(data) > MAX_MEDIA_BYTES:
        raise ValueError(f"decoded media exceeds {MAX_MEDIA_BYTES} bytes")
    with _hidden_run() as run:
        path = run / filename
        path.write_bytes(data)
        args = [
            "docs", "+media-insert", "--as", "user", "--doc", doc,
            "--file", "./" + path.resolve().relative_to(PROJECT_ROOT).as_posix(),
            "--type", media_type, "--format", "json",
        ]
        if selection:
            args.extend(["--selection-with-ellipsis", selection])
        if before:
            args.append("--before")
        return _run_cli(args, "insert_media")


@mcp.tool(title="Read a Lark whiteboard", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def whiteboard_query(
    whiteboard_token: str, output_as: Literal["code", "raw"] = "code",
) -> dict:
    """Read one Lark whiteboard as Mermaid/PlantUML code or raw nodes."""
    _check_lark_cli()
    if output_as not in {"code", "raw"}:
        raise ValueError("output_as must be code or raw")
    args = [
        "whiteboard", "+query", "--as", "user",
        "--whiteboard-token", whiteboard_token,
        "--output_as", output_as, "--format", "json",
    ]
    for attempt in range(5):
        try:
            return _run_cli(args, "whiteboard_query")
        except RuntimeError as error:
            if "4003101" not in str(error) or attempt == 4:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


@mcp.tool(title="Update a Lark whiteboard", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def whiteboard_update(
    whiteboard_token: str,
    source: str,
    input_format: Literal["mermaid", "plantuml", "raw"] = "mermaid",
    overwrite: bool = True,
) -> dict:
    """Update one Lark whiteboard from Mermaid, PlantUML, or raw node JSON."""
    _check_lark_cli()
    if input_format not in {"mermaid", "plantuml", "raw"}:
        raise ValueError("input_format must be mermaid, plantuml, or raw")
    with _hidden_run() as run:
        payload = _payload(run / ".whiteboard-source", source)
        args = [
            "whiteboard", "+update", "--as", "user",
            "--whiteboard-token", whiteboard_token,
            "--input_format", input_format, "--source", payload,
            "--format", "json",
        ]
        if overwrite:
            args.append("--overwrite")
        return _run_cli(args, "whiteboard_update")


@mcp.tool(title="Schedule Lark-Markdown MCP restart", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False})
def schedule_mcp_restart(
    confirmation: str,
    delay_seconds: int = 10,
) -> dict:
    """Schedule a restart of this MCP service after the current request completes."""
    if confirmation != RESTART_CONFIRMATION:
        raise ValueError(f"confirmation must exactly equal {RESTART_CONFIRMATION}")
    if not 5 <= delay_seconds <= 300:
        raise ValueError("delay_seconds must be between 5 and 300")
    if not RESTART_WORKER.is_file():
        raise RuntimeError(f"restart worker is missing: {RESTART_WORKER}")
    try:
        worker = subprocess.Popen(
            [sys.executable, str(RESTART_WORKER), "--delay", str(delay_seconds)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise RuntimeError(f"could not schedule MCP restart: {error}") from error
    return {
        "status": "scheduled",
        "service": "lark-markdown-mcp.service",
        "delay_seconds": delay_seconds,
        "worker_pid": worker.pid,
        "message": "The service will restart after the delay; the current tool call is complete.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    _cleanup_stale_runs()
    if args.transport == "http":
        uvicorn_config = _https_config(args.host, args.tls_cert, args.tls_key)
        mcp.run(
            transport="http", host=args.host, port=args.port,
            uvicorn_config=uvicorn_config,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
