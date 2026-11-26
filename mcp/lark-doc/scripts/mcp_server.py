#!/usr/bin/env python3
"""Authenticated lark-cli tools for Docx, Wiki, Sheets, Base and whiteboards."""
from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import ipaddress
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.middleware import AuthMiddleware
from starlette.routing import Route

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
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Alark-doc/oauth/callback"
GROK_REDIRECT_URI = "https://grok.com/connectors-oauth-exchange-code/"
CURSOR_REDIRECT_URI = "https://cursor.com/agents/mcp/oauth/callback"
# FastMCP 4 checks every DCR and CIMD redirect URI against this list, and one
# unknown URI fails the whole registration. Keep each client's full set.
# Loopback entries match any port (RFC 8252) and cover Claude Code, Codex CLI,
# Gemini CLI, Cursor desktop, VS Code and other local clients.
ALLOWED_CLIENT_REDIRECT_URIS = [
    # Claude web, Desktop connectors, mobile and Cowork; claude.com is the
    # announced successor domain.
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    # Claude Desktop plugin servers that register through DCR.
    "claude://claude.ai/mcp-auth-callback/sdk",
    # ChatGPT and Codex hosted connectors: stable callback when the server
    # supports RFC 9207 issuer identification, otherwise per connection.
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/*",
    # Gemini web registers all three hosts with both paths in one request.
    "https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-*",
    "https://oauth-redirect.googleusercontent.com/a/user_bound_custom-mcp-*",
    "https://oauth-redirect-sandbox.googleusercontent.com/r/user_bound_custom-mcp-*",
    "https://oauth-redirect-sandbox.googleusercontent.com/a/user_bound_custom-mcp-*",
    "https://oauth-redirect-test.googleusercontent.com/r/user_bound_custom-mcp-*",
    "https://oauth-redirect-test.googleusercontent.com/a/user_bound_custom-mcp-*",
    # Gemini Enterprise custom MCP data stores.
    "https://vertexaisearch.cloud.google.com/oauth-redirect",
    # Google Antigravity, for both its CIMD client and DCR.
    "https://antigravity.google/oauth-callback",
    # Grok and the xAI console.
    GROK_REDIRECT_URI,
    "https://console.x.ai/connectors-oauth-exchange-code/",
    # Cursor web and Cloud Agents, and older Cursor desktop releases.
    CURSOR_REDIRECT_URI,
    "https://www.cursor.com/agents/mcp/oauth/callback",
    "cursor://anysphere.cursor-mcp/oauth/callback",
    # VS Code for the web.
    "https://vscode.dev/redirect",
    WORKBUDDY_REDIRECT_URI,
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://[::1]:*",
]
CONFIDENTIAL_AUTH_METHODS = ("client_secret_post", "client_secret_basic")
AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
CLI_TIMEOUT_SECONDS = 60
MAX_BATCH_ITEMS = 100
DEFAULT_BATCH_CONCURRENCY = 4
MAX_BATCH_CONCURRENCY = 8
MAX_CONTENT_BYTES = 10 * 1024 * 1024
MAX_PATTERN_BYTES = 128 * 1024
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
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
EMPHASIS_RUN = re.compile(r"(?<!\*)\*{1,2}(?!\*)")
BLOCK_TAG = re.compile(
    r"</?(h[1-9]|p|ul|ol|li|table|thead|tbody|tr|td|th|blockquote|pre|callout|grid|column|"
    r"checkbox|hr|img|whiteboard|bookmark|sheet|task|chat_card|title)\b",
    re.IGNORECASE,
)
CALLOUT_EMOJI = re.compile(r'<callout\b[^>]*?\bemoji="([^"]+)"')
# "## 1. Title", "### 1.2 Title"; a bare "## 2024 年" has no dot and is left alone.
NUMBERED_HEADING = re.compile(r"(?m)^(#{1,9})[ \t]+\d+\.(?:\d+\.?)*[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESTART_CONFIRMATION = "RESTART_LARK_DOC_MCP"
RESTART_WORKER = PROJECT_ROOT / "scripts" / "restart_mcp_worker.py"
APP_SETUP_CONFIRMATION = "CREATE_LARK_APP"
APP_SETUP_URL_TIMEOUT_SECONDS = 30
APP_SETUP_COMPLETE_TIMEOUT_SECONDS = 5
APP_SETUP_URL = re.compile(rb"https://[^\s\x1b]+")

DocFormat = Literal["markdown", "xml"]
CommentFileType = Literal["doc", "docx", "sheet", "file", "slides", "bitable", "base", "apps", "wiki"]
CommentPageSize = Annotated[int, Field(ge=1, le=100)]


def _is_punct(char: str) -> bool:
    return unicodedata.category(char)[0] in "PS"


def _fix_emphasis_line(line: str) -> str:
    """Move edge punctuation outside */** pairs whose outer side touches text.

    CommonMark (and Lark, verified for abc**"x"**def and 文字*“x”*文字) refuses to
    open/close emphasis there, leaving literal asterisks. Moving the punctuation out
    keeps the meaning and always renders. ponytail: pairs within one line only.
    """
    code = [match.span() for match in INLINE_CODE.finditer(line)]
    stack: list[re.Match[str]] = []
    pairs: list[tuple[re.Match[str], re.Match[str]]] = []
    for run in EMPHASIS_RUN.finditer(line):
        if any(start <= run.start() < end for start, end in code):
            continue
        before = line[run.start() - 1] if run.start() else " "
        after = line[run.end()] if run.end() < len(line) else " "
        if stack and len(stack[-1].group()) == len(run.group()) and not before.isspace():
            pairs.append((stack.pop(), run))
        elif not after.isspace():
            stack.append(run)
    edits: list[tuple[int, int, str]] = []
    for opener, closer in pairs:
        marker = opener.group()
        inner = line[opener.end():closer.start()]
        lead = next((i for i, c in enumerate(inner) if not _is_punct(c)), len(inner))
        trail = next((i for i, c in enumerate(reversed(inner)) if not _is_punct(c)), len(inner))
        if lead == len(inner):
            continue
        outside_before = line[opener.start() - 1] if opener.start() else " "
        outside_after = line[closer.end()] if closer.end() < len(line) else " "
        if trail and not outside_after.isspace() and not _is_punct(outside_after):
            edits.append((closer.start() - trail, closer.end(), marker + inner[len(inner) - trail:]))
        if lead and not outside_before.isspace() and not _is_punct(outside_before):
            edits.append((opener.start(), opener.end() + lead, inner[:lead] + marker))
    for start, end, text in sorted(edits, reverse=True):
        line = line[:start] + text + line[end:]
    return line


def _inline_markdown_to_xml(text: str) -> str:
    """Convert the inline Markdown a heading may carry; Lark does not parse it inside XML tags."""
    parts: list[str] = []
    last = 0
    for match in INLINE_CODE.finditer(text):
        parts.append(_inline_prose_to_xml(text[last:match.start()]))
        parts.append(f"<code>{html.escape(match.group(2), quote=False)}</code>")
        last = match.end()
    parts.append(_inline_prose_to_xml(text[last:]))
    return "".join(parts)


def _inline_prose_to_xml(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>', text)
    for pattern, tag in (
        (r"\*\*(.+?)\*\*", "b"), (r"__(.+?)__", "b"), (r"~~(.+?)~~", "del"),
        (r"\*(.+?)\*", "em"), (r"\$(.+?)\$", "latex"),
    ):
        text = re.sub(pattern, rf"<{tag}>\1</{tag}>", text)
    return text


def _auto_number_headings(prose: str, *, document_start: bool) -> str:
    """Turn hand-numbered headings (## 1.2 Title) into Lark auto-numbered headings.

    The leading H1 of a document becomes its title, so it is left alone.
    """
    first_line = prose.lstrip("\n").split("\n", 1)[0] if document_start else None

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == first_line and len(match.group(1)) == 1:
            return match.group(0)
        level = len(match.group(1))
        return f'<h{level} seq="auto">{_inline_markdown_to_xml(match.group(2))}</h{level}>'

    return NUMBERED_HEADING.sub(replace, prose)


def _center_formulas(prose: str) -> str:
    return DISPLAY_FORMULA.sub(
        lambda match: f'<p align="center"><latex>{html.escape(match.group(1).strip(), quote=False)}</latex></p>',
        prose,
    )


def _center_display_math(content: str) -> str:
    """Convert standalone Markdown display formulas, excluding fenced code blocks."""
    return _map_prose(content, lambda prose, _: _center_formulas(prose))


def _map_prose(content: str, convert) -> str:
    """Apply convert(prose, is_document_start) to text outside fenced code blocks."""
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
            output.append(convert("".join(prose), not output))
            prose.clear()
            marker = match.group(1)
            closing = re.compile(rf"^[ ]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
            output.append(line)
        else:
            prose.append(line)
    output.append(convert("".join(prose), not output))
    return "".join(output)


def _normalize_markdown(content: str, *, document_start: bool) -> str:
    def convert(prose: str, is_start: bool) -> str:
        prose = "".join(_fix_emphasis_line(line) for line in prose.splitlines(keepends=True))
        prose = _auto_number_headings(prose, document_start=document_start and is_start)
        return _center_formulas(prose)

    return _map_prose(content, convert)


def _render(content: str, doc_format: DocFormat, *, document_start: bool = False) -> str:
    """Return exactly what will be stored, so preflight can simulate against it.

    Markdown only: fixes emphasis next to punctuation, turns hand-numbered headings
    into Lark auto numbering, and centers standalone $$ formulas.
    """
    return _normalize_markdown(content, document_start=document_start) if doc_format == "markdown" else content


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

    async def register_client(self, client_info) -> None:
        """Keep the secret the SDK issued to a confidential DCR client.

        FastMCP 4 stores every registered client as public and drops the issued
        secret. Gemini web registers with client_secret_post and expects a secret
        back. Store and return it so the token endpoint enforces it, as FastMCP 3
        did; public clients keep FastMCP's behavior.
        """
        method = client_info.token_endpoint_auth_method
        secret = client_info.client_secret
        expires_at = client_info.client_secret_expires_at
        await super().register_client(client_info)
        if method not in CONFIDENTIAL_AUTH_METHODS or not secret:
            return
        client = await self._client_store.get(key=client_info.client_id)
        if client is None:
            raise RuntimeError("registered client was not stored")
        for target in (client, client_info):
            target.token_endpoint_auth_method = method
            target.client_secret = secret
            target.client_secret_expires_at = expires_at
        await self._client_store.put(key=client_info.client_id, value=client)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        for index, route in enumerate(routes):
            if isinstance(route, Route) and route.path == AUTHORIZATION_SERVER_METADATA_PATH:
                routes[index] = Route(
                    route.path,
                    endpoint=_ConfidentialAuthMethodsMetadata(route.endpoint),
                    methods=route.methods,
                    name=route.name,
                    include_in_schema=route.include_in_schema,
                )
        return routes


class _ConfidentialAuthMethodsMetadata:
    """ASGI wrapper that adds the secret-based token endpoint auth methods."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        start = None
        body = bytearray()

        async def capture(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body.extend(message.get("body", b""))
            if message.get("more_body"):
                return
            payload = bytes(body)
            if start["status"] == 200:
                metadata = json.loads(payload)
                methods = metadata.get("token_endpoint_auth_methods_supported") or []
                metadata["token_endpoint_auth_methods_supported"] = [
                    *CONFIDENTIAL_AUTH_METHODS,
                    *(method for method in methods if method not in CONFIDENTIAL_AUTH_METHODS),
                ]
                payload = json.dumps(metadata, separators=(",", ":")).encode()
            headers = [
                (name, value) for name, value in start.get("headers", [])
                if name.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(payload)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": payload})

        await self.app(scope, receive, capture)

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
        "client_id": "lark-doc",
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
    name="lark-doc",
    version="0.19.0",
    instructions=(
        "For normal document work, use the connected tools and never configure or start a server. "
        "Call check_lark_cli only when connection or user auth is uncertain. If it reports "
        "app_not_configured, ask the user before calling begin_lark_app_setup, return its URL and "
        "QR code, and call complete_lark_app_setup only after the user finishes browser setup. "
        "Use begin_lark_auth "
        "and complete_lark_auth only to recover missing user authorization. When the target document "
        "is unknown, call search_documents first to rank candidates by keyword relevance across docs, "
        "wiki, and sheets, then call find_document_text on the chosen doc; never guess a doc token. "
        "Before a local edit, use "
        "find_document_text to return bounded snippets; if it finds multiple targets, refine the query with "
        "longer exact text and never guess. Use point_update or batch_point_update only for exact targets; "
        "they reject non-unique patterns. Write tools raise when lark-cli reports a failed write, and the "
        "error reports the post-failure document state: after any reported write failure, verify with "
        "find_document_text before retrying, and never retry when the state shows the write partially "
        "applied or truncated the document. Use batch_pull only when full-document understanding is "
        "requested, the target cannot be "
        "narrowed by snippets, or XML/native-block structure is needed; on long documents prefer "
        "scope=outline, keyword, or section over a full pull. To write a block type whose XML you are "
        "unsure of (callout, table, columns, ...), read an existing one with batch_pull(doc_format=xml) "
        "and mirror its tags. Use batch_push only for an explicit "
        "whole-document replacement or append. "
        "After point_update, verify with find_document_text using the "
        "replacement (or the removed text for deletion); use batch_pull only after an error, "
        "a format-sensitive operation, or an explicit verification request. "
        "Use list_document_comments to read comment bodies, replies and available anchors. "
        "Use list_document_comment_replies when a comment card has more replies. "
        "Use batch_pull(include_comments=true) for compact comment context alongside the document. "
        "Use sheets_info before sheets_read/sheets_write; these tools handle electronic spreadsheets. "
        "Use base_inspect for multidimensional Base tables/fields/views, base_query_records to read "
        "and base_write_records to write records. Do not use Docx tools on sheet/Base resources. "
        "Preserve select options and full field definitions on Base field updates. "
        "Use sheets_sheet_info plus nearby cells/formulas/validation before structural edits; "
        "refetch coordinates afterwards. sheets_sort_range must cover full records. "
        "Use sheets_search_cells before sheets_replace_cells and verify remaining matches afterwards. "
        "For value-dependent highlighting use sheets_write_conditional_format, then verify both "
        "sheets_list_conditional_formats and sheets_get_conditional_format_result. "
        "After uncertain creates/appends, inspect the remote state before retrying. "
        "Every doc parameter accepts a document URL or token. Use scan_document_assets to list "
        "images and whiteboards in a Lark document without returning its full XML. "
        "point_update only replaces text and cannot change block attributes; to replace, insert, delete, "
        "or move whole blocks (headings, paragraphs, lists, tables), call find_document_text with "
        "doc_format=xml and detail=with-ids to get block IDs, then call block_update. "
        "Headings: prefer Lark auto numbering with heading levels that never skip (h1, then h2, ...). "
        "In XML write <h2 seq=\"auto\">Title</h2> without a typed number; Markdown writes convert "
        "hand-numbered headings such as \"## 1. Title\" or \"### 1.2 Title\" automatically (except a "
        "document-opening \"# \" title). "
        "Emphasis in Markdown: write bold as **text**, never __text__, which does not render between "
        "CJK characters. Markdown writes move punctuation out of */** when the outer side touches text "
        "(abc**\"q\"**def becomes abc\"**q**\"def), so that punctuation is not bolded; to bold it, use "
        "XML <b>…</b>. "
        "Callout emoji: default to emoji=\"💡\" and use another emoji only when the user asks for it. "
        "Lark accepts only its own emoji set and silently swaps others (e.g. ⚠️) for 💡. "
        "batch_push, block_update, and create_document then return format_warnings; the content is "
        "already written, so fix only that callout with block_update instead of rewriting or recreating. "
        "create_document and insert_media are not idempotent: after an error or interruption, look for "
        "the result (search_documents by title, or find_document_text) before calling again."
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
# one setup flow per MCP process; persist it only if multi-worker setup is needed.
_app_setup_lock = threading.Lock()
_app_setup: tuple[subprocess.Popen[bytes], str] | None = None


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


def _https_config(
    host: str, cert: Path | None, key: Path | None, allow_unauthenticated: bool = False,
) -> dict[str, str]:
    if bool(cert) != bool(key):
        raise RuntimeError("--tls-cert and --tls-key must be supplied together")
    if AUTH_PROVIDER is None and not allow_unauthenticated:
        # A loopback bind is not a safety property: the documented deployment
        # puts nginx in front of 127.0.0.1, so a missing LARK_MCP_AUTH_MODE
        # would otherwise publish an unauthenticated server to the internet.
        raise RuntimeError(
            f"HTTP transport requires configured authentication: set {AUTH_MODE_ENV} "
            "(github or token), or pass --allow-unauthenticated for local-only use"
        )
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
        # The timeout kills the local CLI process, not the request already in
        # flight to Lark, so the write may well have applied.
        raise LarkCLIError({
            "operation": operation,
            "error": "timeout",
            "timeout_seconds": CLI_TIMEOUT_SECONDS,
            "next_step": (
                "this operation may already have applied server-side; verify with "
                "find_document_text before retrying. Retrying batch_push with "
                "mode=append duplicates content."
            ),
        }) from error
    except OSError as error:
        raise LarkCLIError({
            "operation": operation,
            "error": "spawn_failed",
            "message": str(error),
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


def _not_configured(error: LarkCLIError) -> bool:
    try:
        payload = json.loads(str(error.details.get("message", "")))
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("error"), dict)
        and payload["error"].get("subtype") == "not_configured"
    )


def _lark_app_configured() -> bool:
    try:
        _run_process(["lark-cli", "config", "show"], "check Lark app configuration")
    except LarkCLIError as error:
        if _not_configured(error):
            return False
        raise
    return True


def _start_lark_app_setup(brand: str, lang: str) -> subprocess.Popen[bytes]:
    executable = shutil.which("lark-cli")
    if not executable:
        raise LarkCLIError({
            "operation": "start Lark app setup",
            "error": "not_installed",
            "message": "lark-cli is not installed or not on PATH",
        })
    try:
        return subprocess.Popen(
            [executable, "config", "init", "--new", "--brand", brand, "--lang", lang],
            cwd=PROJECT_ROOT,
            env={**os.environ, **QUIET_ENV},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as error:
        raise LarkCLIError({
            "operation": "start Lark app setup",
            "error": "spawn_failed",
            "message": str(error),
        }) from error


def _read_lark_app_setup_url(process: subprocess.Popen[bytes]) -> str:
    if process.stdout is None:
        raise RuntimeError("lark-cli setup output is unavailable")
    deadline = time.monotonic() + APP_SETUP_URL_TIMEOUT_SECONDS
    output = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select(
            [process.stdout], [], [], max(0, deadline - time.monotonic()),
        )
        if ready:
            chunk = os.read(process.stdout.fileno(), 4096)
            output.extend(chunk)
            match = APP_SETUP_URL.search(output)
            if match:
                return match.group().decode("utf-8")
        if process.poll() is not None:
            break
    raise LarkCLIError({
        "operation": "start Lark app setup",
        "error": "verification_url_unavailable",
        "exit_code": process.poll(),
        "timeout_seconds": APP_SETUP_URL_TIMEOUT_SECONDS,
    })


def _stop_lark_app_setup(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _app_setup_response(status: str, verification_url: str) -> dict:
    return {
        "status": status,
        "verification_url": verification_url,
        "qr_code_png_base64": _lark_auth_qrcode(verification_url),
        "next_step": (
            "Open the verification URL or scan the QR code. After the browser reports success, "
            "call complete_lark_app_setup, then begin_lark_auth."
        ),
    }


def _check_lark_cli(use_cache: bool = True) -> dict:
    global _auth_cache
    cache = _auth_cache  # One read: complete_lark_auth may clear it concurrently.
    if use_cache and cache and time.monotonic() < cache[0]:
        return cache[1]
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
        try:
            auth = _run_cli(
                ["auth", "status", "--json", "--verify"],
                "check lark-cli user authentication",
            )
        except LarkCLIError as error:
            if _not_configured(error):
                raise LarkCLIError({
                    "operation": "check Lark app configuration",
                    "error": "app_not_configured",
                    "message": "lark-cli is not bound to a Lark app",
                    "next_step": "ask the user, then call begin_lark_app_setup",
                }) from error
            raise
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
                # The operation already succeeded. Raising here would report a
                # completed write as a failure and invite a retry that appends
                # the same content twice. _cleanup_stale_runs reclaims the
                # directory on the next start.
                print(f"lark-doc: {message}", file=sys.stderr)


def _cleanup_stale_runs() -> None:
    if WORKDIR.is_symlink():
        raise RuntimeError("refusing to clean a symlinked .lark_publish directory")
    if not WORKDIR.exists():
        return
    # age heuristic, not a lock. It keeps a second instance sharing
    # PROJECT_ROOT from deleting payloads it is still writing, because no call
    # outlives CLI_TIMEOUT_SECONDS. Use a lockfile only if sharing is by design.
    cutoff = time.time() - CLI_TIMEOUT_SECONDS * 2
    for path in WORKDIR.glob(".run-*"):
        if path.stat().st_mtime < cutoff:
            shutil.rmtree(path)


def _payload(path: Path, content: str) -> str:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
    path.write_text(content, encoding="utf-8")
    return "@./" + path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _validate_pattern(pattern: str) -> None:
    """Patterns travel as argv, so they must stay well under ARG_MAX."""
    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern.encode("utf-8")) > MAX_PATTERN_BYTES:
        raise ValueError(f"pattern exceeds {MAX_PATTERN_BYTES} bytes")


def _reject_block_markup(text: str, field: str) -> None:
    """str_replace edits inline text only; block tags in it are dropped while the write reports success."""
    match = BLOCK_TAG.search(text)
    if match:
        raise ValueError(
            f"{field} contains block markup <{match.group(1)}>; point updates only change inline text. "
            "Use find_document_text(doc_format=\"xml\", detail=\"with-ids\") and block_update instead"
        )


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


def _assert_write_succeeded(result: dict, operation: str) -> None:
    """Raise when lark-cli reports a write as failed; never relay degradation as success.

    lark-cli exits 0 but returns data.result="failed" with a degrade warning when a write
    degrades (e.g. str_replace pattern not found). Relaying that as a normal result hid
    document corruption and let batch_point_update keep writing into a damaged document.
    Treat any explicit non-success result as a hard failure so callers stop and verify.
    Envelopes without a result field (some unit-test mocks) pass through unchanged.
    """
    if not isinstance(result, dict):
        return
    ok = result.get("ok", True)
    data = result.get("data")
    status = data.get("result") if isinstance(data, dict) else None
    if ok is False or (isinstance(status, str) and status.casefold() != "success"):
        warnings = data.get("warnings") if isinstance(data, dict) else None
        if isinstance(warnings, str):
            warnings = [warnings]
        message = "; ".join(str(w) for w in (warnings or [])) if warnings else str(status or "failed")
        if not message:
            message = json.dumps(result, ensure_ascii=False)[:4000]
        raise LarkCLIError({
            "operation": operation,
            "error": "lark_write_failed",
            "result": status,
            "message": message,
            "next_step": (
                "The write was reported as failed and may have partially modified the "
                "document. Verify with find_document_text before retrying; only retry "
                "when the document state shows the write did not apply."
            ),
        })


def _callout_emoji_warnings(doc: str, content: str) -> list[str]:
    """Lark silently swaps unsupported callout emoji (e.g. ⚠️) for 💡; report it after a write.

    Best effort: the write already succeeded, so a failed check returns no warnings
    instead of raising and inviting a duplicate retry.
    """
    requested = {emoji for emoji in CALLOUT_EMOJI.findall(content) if emoji != "💡"}
    if not requested:
        return []
    try:
        stored = _fetch_document(doc, "xml", "full", "callout emoji check").get("content", "")
    except Exception:  # noqa: BLE001 - verification must never fail a completed write
        return []
    present = set(CALLOUT_EMOJI.findall(stored if isinstance(stored, str) else ""))
    return [
        f"callout emoji {emoji!r} is not supported by Lark and was replaced with 💡; pick another emoji"
        for emoji in sorted(requested - present)
    ]


def _assess_document_state(
    doc: str,
    doc_format: DocFormat,
    original_content: str,
    rendered: str,
    operation: str,
) -> dict:
    """Best-effort document state after a failed write; never raises.

    Detects the two failure signatures of lark-cli write degradation: a partial apply
    (the intended replacement is present despite the failure) and destructive truncation
    (the document tail from before the write is missing).
    """
    try:
        document = _fetch_document(doc, doc_format, "simple", f"{operation} damage assessment")
        current = document.get("content", "")
        current_text = current if isinstance(current, str) else ""
        revision_id = _revision_id(document, f"{operation} damage assessment")
        tail = original_content[-120:] if isinstance(original_content, str) else ""
        return {
            "revision_id": revision_id,
            "intended_replacement_present": bool(rendered) and rendered in current_text,
            "original_tail_preserved": bool(tail) and tail in current_text,
        }
    except Exception as error:  # Assessment must never mask the original write failure.
        return {"error": "damage_assessment_unavailable", "message": str(error)}


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


@mcp.tool(title="Start Lark app setup", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def begin_lark_app_setup(
    confirmation: str,
    brand: Literal["feishu", "lark"] = "feishu",
    lang: str = "zh_cn",
) -> dict:
    """Create and bind one Lark app after explicit user confirmation."""
    global _app_setup
    if confirmation != APP_SETUP_CONFIRMATION:
        raise ValueError(f"confirmation must exactly equal {APP_SETUP_CONFIRMATION}")
    if brand not in {"feishu", "lark"}:
        raise ValueError("brand must be feishu or lark")
    if not lang.strip():
        raise ValueError("lang must not be empty")
    with _app_setup_lock:
        if _lark_app_configured():
            return {
                "status": "already_configured",
                "next_step": "call begin_lark_auth if user authorization is missing",
            }
        if _app_setup and _app_setup[0].poll() is None:
            return _app_setup_response("pending", _app_setup[1])
        _app_setup = None
        process = _start_lark_app_setup(brand, lang)
        try:
            verification_url = _read_lark_app_setup_url(process)
            response = _app_setup_response("pending", verification_url)
        except Exception:
            _stop_lark_app_setup(process)
            raise
        _app_setup = (process, verification_url)
        return response


@mcp.tool(title="Complete Lark app setup", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def complete_lark_app_setup() -> dict:
    """Verify that browser-based app creation finished and lark-cli saved the binding."""
    global _app_setup, _auth_cache
    with _app_setup_lock:
        if not _app_setup:
            if _lark_app_configured():
                return {
                    "status": "configured",
                    "next_step": "call begin_lark_auth to authorize Docs, Drive, Wiki, Sheets, and Base",
                }
            raise LarkCLIError({
                "operation": "complete Lark app setup",
                "error": "setup_not_started",
                "next_step": "call begin_lark_app_setup after explicit user confirmation",
            })
        process, verification_url = _app_setup
        try:
            process.communicate(timeout=APP_SETUP_COMPLETE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return _app_setup_response("pending", verification_url)
        _app_setup = None
        if process.returncode:
            raise LarkCLIError({
                "operation": "complete Lark app setup",
                "error": "lark_cli_failed",
                "exit_code": process.returncode,
                "next_step": "call begin_lark_app_setup to start a fresh setup flow",
            })
        if not _lark_app_configured():
            raise LarkCLIError({
                "operation": "complete Lark app setup",
                "error": "configuration_missing",
                "next_step": "call begin_lark_app_setup to start a fresh setup flow",
            })
        _auth_cache = None
        return {
            "status": "configured",
            "next_step": "call begin_lark_auth to authorize Docs, Drive, Wiki, Sheets, and Base",
        }


@mcp.tool(title="Start Lark user authorization", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def begin_lark_auth() -> dict:
    """Create a one-time Lark device authorization URL and QR code for Docs, Drive, Wiki, Sheets, and Base."""
    payload = _run_cli(
        ["auth", "login", "--domain", "docs", "--domain", "drive", "--domain", "wiki", "--domain", "sheets", "--domain", "base", "--no-wait", "--json"],
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
    scope_args: list[str] | None = None,
) -> dict:
    result = _run_cli([
        "docs", "+fetch", "--api-version", "v2", "--as", "user",
        "--doc", doc, "--doc-format", doc_format, "--detail", detail,
        *(scope_args or []), "--format", "json",
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


def _scope_args(
    scope: str, start_block_id: str | None, end_block_id: str | None,
    keyword: str | None, max_depth: int | None, document_count: int,
) -> list[str]:
    if scope not in {"full", "outline", "section", "range", "keyword"}:
        raise ValueError("scope must be full, outline, section, range, or keyword")
    if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < -1):
        raise ValueError("max_depth must be an integer >= -1")
    needs_start = scope in {"section", "range"}
    if needs_start and document_count != 1:
        raise ValueError(f"scope={scope} uses block IDs and takes exactly one document")
    if needs_start != bool(start_block_id):
        raise ValueError(f"start_block_id is {'required' if needs_start else 'only valid'} for section and range")
    if (scope == "range") != bool(end_block_id):
        raise ValueError(f"end_block_id is {'required' if scope == 'range' else 'only valid'} for range")
    if (scope == "keyword") != bool(keyword):
        raise ValueError(f"keyword is {'required' if scope == 'keyword' else 'only valid'} for scope=keyword")
    if scope == "full":
        return []
    args = ["--scope", scope]
    for flag, value in (("--start-block-id", start_block_id), ("--end-block-id", end_block_id), ("--keyword", keyword)):
        if value:
            args.extend([flag, value])
    if max_depth is not None:
        args.extend(["--max-depth", str(max_depth)])
    return args


@mcp.tool(title="Batch pull Lark documents", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def batch_pull(
    documents: list[str],
    doc_format: DocFormat = "markdown",
    detail: Literal["simple", "with-ids", "full"] = "simple",
    concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    scope: Literal["full", "outline", "section", "range", "keyword"] = "full",
    start_block_id: str | None = None,
    end_block_id: str | None = None,
    keyword: str | None = None,
    max_depth: int | None = None,
    include_comments: bool = False,
) -> list[dict]:
    """Fetch documents concurrently; results remain in input order.

    Scope avoids pulling a whole long document: "outline" lists headings, "keyword" returns
    blocks matching keyword (case-insensitive substring, "a|b" for OR), "section" expands
    from heading start_block_id, "range" reads start_block_id..end_block_id. section and
    range need block IDs, so they take exactly one document. max_depth: for outline the
    deepest heading level kept (2 = h1..h2); for other scopes the subtree depth
    (-1 unlimited, 0 block only).

    detail: "simple" plain content, "with-ids" adds block IDs, "full" also adds styles
    and edit metadata. documents: URLs or tokens, at most 100; concurrency 1-8.

    Use Markdown/simple for ordinary text. Use XML/full when the task depends on
    formatting or native structure that Markdown cannot reliably expose, including
    highlights, text/background colors, underline, callouts, columns, references,
    bookmarks, URL previews, buttons, reminders, whiteboards, block IDs, style
    attributes, or reference metadata. Highlights are returned as
    <span background-color="..."> in XML but degrade to plain text in Markdown.

    include_comments returns the compact comment content supplied by document fetch.
    Use list_document_comments for paginated results or when fetch truncates comments.
    """
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if detail not in {"simple", "with-ids", "full"}:
        raise ValueError("detail must be simple, with-ids, or full")
    _validate_batch(documents, "documents")
    scope_args = _scope_args(scope, start_block_id, end_block_id, keyword, max_depth, len(documents))
    workers = _batch_workers(concurrency, len(documents))
    for index, doc in enumerate(documents):
        if not isinstance(doc, str) or not doc.strip():
            raise _batch_failure(
                "batch_pull", index, "", 0, ValueError("document must not be empty"),
            )
    def pull_one(index: int, doc: str) -> dict:
        try:
            document = _fetch_document(
                doc, doc_format, detail, f"batch_pull documents[{index}]", scope_args,
            )
        except RuntimeError as error:
            raise _batch_failure("batch_pull", index, doc, 0, error) from error
        result = {
            "doc": doc,
            "revision_id": document.get("revision_id"),
            "content": document.get("content", ""),
        }
        if include_comments:
            reference_map = document.get("reference_map") or {}
            result["comments"] = reference_map.get("comments") or {}
        return result

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


def _read_comment_page(
    command: str,
    doc: str,
    file_type: CommentFileType | None,
    page_size: int,
    page_token: str | None,
    options: list[str],
) -> dict:
    doc = doc.strip()
    if not doc:
        raise ValueError("doc must not be empty")
    is_url = doc.startswith(("https://", "http://"))
    if not is_url and not file_type:
        raise ValueError("file_type is required for a bare document or Wiki token")
    args = [
        "drive", command, "--as", "user", "--format", "json",
        f"--{'url' if is_url else 'token'}={doc}", f"--page-size={page_size}",
        *options,
    ]
    if file_type:
        args.append(f"--type={file_type}")
    if page_token:
        args.append(f"--page-token={page_token}")
    _check_lark_cli()
    result = _run_cli(args, f"drive {command}")
    if result.get("ok") is False:
        raise LarkCLIError({"operation": command, "error": "remote_error", "result": result})
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise LarkCLIError({
            "operation": command,
            "error": "invalid_response",
            "message": "missing data.items array",
        })
    return data


@mcp.tool(title="Read Lark document comments", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def list_document_comments(
    doc: str,
    file_type: CommentFileType | None = None,
    solved_status: Literal["false", "true", "all"] = "false",
    comment_scope: Literal["all", "whole", "partial"] = "all",
    page_size: CommentPageSize = 50,
    page_token: str | None = None,
) -> dict:
    """Read one page of comment cards from a document URL (including Wiki) or token.

    For a bare token, supply file_type (wiki for a Wiki node). solved_status defaults
    to unresolved ("false"); use "true" for resolved or "all" for both. comment_scope
    selects whole-document comments, selected-content comments ("partial"), or both.

    items[].reply_list.replies[0].content.elements holds the comment body, followed
    by any included replies. quote is the cited document text. For Docx, available
    relation fields identify the comment's location. Top-level has_more/page_token
    paginate cards. When items[].has_more is true, use list_document_comment_replies.
    """
    return _read_comment_page(
        "+list-comments", doc, file_type, page_size, page_token,
        [f"--solved-status={solved_status}", f"--comment-scope={comment_scope}", "--need-relation"],
    )


@mcp.tool(title="Read Lark document comment replies", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def list_document_comment_replies(
    doc: str,
    comment_id: str,
    file_type: CommentFileType | None = None,
    page_size: CommentPageSize = 50,
    page_token: str | None = None,
) -> dict:
    """Read one page of messages for a comment_id from list_document_comments.

    Provide a document URL (including Wiki) or a token with file_type. Message
    contents are in items[].content.elements. The first item on the first page
    is the original comment. When has_more is true, pass page_token to continue.
    """
    if not comment_id.strip():
        raise ValueError("comment_id must not be empty")
    return _read_comment_page(
        "+list-replies", doc, file_type, page_size, page_token,
        [f"--comment-id={comment_id.strip()}"],
    )


@mcp.tool(title="Find document text snippets", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def find_document_text(
    doc: str,
    query: str,
    context_chars: int = 240,
    max_matches: int = 3,
    doc_format: DocFormat = "markdown",
    detail: Literal["simple", "with-ids"] = "simple",
) -> dict:
    """Return bounded context around exact text matches without returning the full document.

    Use doc_format="xml" with detail="with-ids" to get the block IDs that block_update needs.
    query is literal text (not regex) matched against the chosen doc_format rendering.
    context_chars 0-1000 per side; max_matches 1-10. The returned revision_id can be
    passed as expected_revision_id to block_update or batch_point_update.
    """
    if not doc.strip():
        raise ValueError("doc must not be empty")
    if not query:
        raise ValueError("query must not be empty")
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if detail not in {"simple", "with-ids"}:
        raise ValueError("detail must be simple or with-ids")
    if type(context_chars) is not int or not 0 <= context_chars <= MAX_SNIPPET_CONTEXT_CHARS:
        raise ValueError(f"context_chars must be an integer between 0 and {MAX_SNIPPET_CONTEXT_CHARS}")
    if type(max_matches) is not int or not 1 <= max_matches <= MAX_SNIPPET_MATCHES:
        raise ValueError(f"max_matches must be an integer between 1 and {MAX_SNIPPET_MATCHES}")
    _check_lark_cli()
    document = _fetch_document(doc, doc_format, detail, "find_document_text")
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

    doc_types: optional comma-separated filter from doc, docx, sheet, bitable, mindnote,
    file, wiki, folder, catalog, slides, shortcut (e.g. "docx,wiki"); an unknown value
    raises. count: 1-20. Search can miss existing docs (index lag or ranking), so an
    empty result is not proof the doc is missing.
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
    """Write independent documents concurrently; results remain in input order.

    Each item: {"doc": URL or token, "content": str, "mode": "overwrite" (default,
    replaces the whole document) | "append", "doc_format": "markdown" (default) | "xml"}.
    At most 100 items, each doc once; concurrency 1-8.

    mode="append" is not idempotent: a retry after a reported failure can append
    the same content twice. Verify with find_document_text before retrying.

    Markdown is normalized before writing: hand-numbered headings ("## 1. Title") become
    Lark auto-numbered headings, punctuation that would break */** is moved outside the
    markers, and standalone $$ formulas are centered. Use **bold**, never __bold__.
    """
    _validate_batch(documents, "documents")
    workers = _batch_workers(concurrency, len(documents))
    seen: set[str] = set()
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
        if doc in seen:
            raise _batch_failure(
                "batch_push", index, doc, 0,
                ValueError("doc appears more than once; concurrent writes would overwrite each other"),
            )
        seen.add(doc)
        content = _render(content, doc_format, document_start=mode == "overwrite")
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
                _assert_write_succeeded(result, f"batch_push documents[{index}]")
            except RuntimeError as error:
                raise _batch_failure("batch_push", index, doc, 0, error) from error
            pushed = {"doc": doc, "result": result}
            warnings = _callout_emoji_warnings(doc, prepared[index][1])
            if warnings:
                pushed["format_warnings"] = warnings
            return pushed

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
    """Write an already-rendered replacement; callers must pass it through _render."""
    with _hidden_run() as run:
        content = _payload(run / ".replacement", replacement)
        result = _run_cli([
            "docs", "+update", "--api-version", "v2", "--as", "user",
            "--doc", doc, "--command", "str_replace", "--pattern", pattern,
            "--revision-id", str(revision_id),
            "--doc-format", doc_format, "--content", content, "--format", "json",
        ], "point_update")
    _assert_write_succeeded(result, "point_update")
    return result


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
    """Replace an exact text target only when it occurs once; the full document stays server-side.

    pattern is literal text, not regex, matched against the doc_format rendering
    (Markdown text or XML source); an empty replacement deletes it. Neither side may
    contain block-level tags; use block_update for those.

    Raises when lark-cli reports the write as failed, including a best-effort
    post-failure document state so the caller can tell a no-op from a partial apply.
    """
    if not doc.strip():
        raise ValueError("doc must not be empty")
    _validate_pattern(pattern)
    _reject_block_markup(pattern, "pattern")
    _reject_block_markup(replacement, "replacement")
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
    rendered = _render(replacement, doc_format)
    try:
        return _write_exact_text(doc, pattern, rendered, doc_format, revision_id)
    except LarkCLIError as error:
        error.details.setdefault("document_state", _assess_document_state(
            doc, doc_format, content, rendered, "point_update",
        ))
        raise


@mcp.tool(title="Update multiple exact document texts", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def batch_point_update(
    doc: str,
    updates: list[dict[str, str]],
    doc_format: DocFormat = "markdown",
    expected_revision_id: int | None = None,
) -> list[dict]:
    """Preflight and apply ordered exact replacements with revision guards.

    Each item: {"pattern": literal text that must occur once, "replacement": str}, applied
    in order. expected_revision_id: revision_id from find_document_text or batch_pull;
    the batch is rejected if the document changed since.

    Stops at the first write that lark-cli reports as failed (never continuing into a
    possibly damaged document) and reports the post-failure document state.
    """
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
        if not isinstance(pattern, str):
            raise _batch_failure("batch_point_update", index, doc, 0, ValueError("pattern must not be empty"))
        if not isinstance(replacement, str):
            raise _batch_failure("batch_point_update", index, doc, 0, ValueError("replacement must be a string"))
        try:
            _validate_pattern(pattern)
            _reject_block_markup(pattern, "pattern")
            _reject_block_markup(replacement, "replacement")
        except ValueError as error:
            raise _batch_failure("batch_point_update", index, doc, 0, error) from error
        # Simulate against what will actually be stored, not the raw replacement.
        prepared.append((pattern, _render(replacement, doc_format)))
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
    for index, (pattern, rendered) in enumerate(prepared):
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
        simulated = simulated.replace(pattern, rendered, 1)
    results = []
    for index, (pattern, rendered) in enumerate(prepared):
        try:
            result = _write_exact_text(doc, pattern, rendered, doc_format, revision_id)
        except Exception as error:
            failure = _batch_failure("batch_point_update", index, doc, len(results), error)
            failure.details.update({
                "error": "revision_conflict" if _is_revision_conflict(error) else "remote_error",
                "expected_revision_id": revision_id,
                "applied_indexes": list(range(len(results))),
            })
            if isinstance(error, LarkCLIError):
                # A degraded write may have partially applied or truncated the document;
                # surface the post-failure state so callers never retry blindly.
                failure.details["document_state"] = _assess_document_state(
                    doc, doc_format, content, rendered, "batch_point_update",
                )
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


BLOCK_COMMANDS = {
    "block_replace", "block_insert_after", "block_delete", "block_move_after", "block_copy_insert_after",
}


def _run_block_command(
    doc: str,
    command: str,
    *,
    content: str | None = None,
    block_id: str | None = None,
    start_block_id: str | None = None,
    end_block_id: str | None = None,
    src_block_ids: str | None = None,
    doc_format: DocFormat = "xml",
    revision_id: int | None = None,
    operation: str = "block_update",
) -> dict:
    args = [
        "docs", "+update", "--api-version", "v2", "--as", "user",
        "--doc", doc, "--command", command, "--doc-format", doc_format, "--format", "json",
    ]
    for flag, value in (
        ("--block-id", block_id), ("--start-block-id", start_block_id),
        ("--end-block-id", end_block_id), ("--src-block-ids", src_block_ids),
    ):
        if value:
            args.extend([flag, value])
    if revision_id is not None:
        args.extend(["--revision-id", str(revision_id)])
    with _hidden_run() as run:
        if content is not None:
            args.extend(["--content", _payload(run / ".content", _render(content, doc_format))])
        result = _run_cli(args, operation)
    _assert_write_succeeded(result, operation)
    return result


@mcp.tool(title="Update document blocks", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def block_update(
    doc: str,
    command: Literal[
        "block_replace", "block_insert_after", "block_delete", "block_move_after", "block_copy_insert_after",
    ],
    content: str | None = None,
    block_id: str | None = None,
    start_block_id: str | None = None,
    end_block_id: str | None = None,
    src_block_ids: str | None = None,
    doc_format: DocFormat = "xml",
    expected_revision_id: int | None = None,
) -> dict:
    """Replace, insert, delete, move, or copy whole blocks by block ID.

    Get IDs first with find_document_text(doc_format="xml", detail="with-ids"); replaced
    and inserted blocks get new IDs, so refetch after any structural edit. doc_format
    defaults to "xml" here, unlike the other write tools. expected_revision_id rejects
    the write if the document changed since that revision. Commands:
    block_replace (content + block_id, or start/end_block_id sibling range),
    block_insert_after (content + block_id; "0" = document start, "-1" = end),
    block_delete (block_id or start/end range), block_move_after and
    block_copy_insert_after (block_id anchor + comma-separated src_block_ids).
    Use this, not point_update, to change block attributes, e.g. turn a heading into
    <h2 seq="auto">Title</h2> for Lark auto numbering. Keep <img>, <whiteboard>,
    <cite>, and other token-bearing tags intact when replacing around them.
    """
    if not doc.strip():
        raise ValueError("doc must not be empty")
    if command not in BLOCK_COMMANDS:
        raise ValueError(f"command must be one of {sorted(BLOCK_COMMANDS)}")
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if expected_revision_id is not None and (
        isinstance(expected_revision_id, bool) or not isinstance(expected_revision_id, int)
        or expected_revision_id < 0
    ):
        raise ValueError("expected_revision_id must be a non-negative integer")
    has_range = bool(start_block_id or end_block_id)
    if has_range and not (start_block_id and end_block_id):
        raise ValueError("start_block_id and end_block_id must be passed together")
    if has_range and block_id:
        raise ValueError("pass block_id or start_block_id/end_block_id, not both")
    if has_range and command not in {"block_replace", "block_delete"}:
        raise ValueError("start/end ranges only apply to block_replace and block_delete")
    if not (block_id or has_range):
        raise ValueError(f"{command} requires block_id" + (
            " or start_block_id/end_block_id" if command in {"block_replace", "block_delete"} else ""
        ))
    needs_content = command in {"block_replace", "block_insert_after"}
    if needs_content and not content:
        raise ValueError(f"{command} requires content")
    if not needs_content and content is not None:
        raise ValueError(f"{command} does not take content")
    needs_src = command in {"block_move_after", "block_copy_insert_after"}
    if needs_src and not src_block_ids:
        raise ValueError(f"{command} requires src_block_ids")
    if not needs_src and src_block_ids:
        raise ValueError(f"{command} does not take src_block_ids")
    _check_lark_cli()
    if expected_revision_id is not None:
        # lark-cli does not reject stale --revision-id on block commands (verified live:
        # revision 3 replaced a block at revision 5), so compare before writing.
        # ponytail: a write landing between this read and ours still slips through.
        current = _revision_id(
            _fetch_document(doc, "markdown", "simple", "block_update preflight"), "block_update preflight",
        )
        if current != expected_revision_id:
            raise LarkCLIError({
                "operation": "block_update",
                "error": "revision_conflict",
                "expected_revision_id": expected_revision_id,
                "actual_revision_id": current,
                "message": "document changed since the block IDs were read; refetch them and retry",
            })
    result = _run_block_command(
        doc, command, content=content, block_id=block_id,
        start_block_id=start_block_id, end_block_id=end_block_id,
        src_block_ids=src_block_ids, doc_format=doc_format, revision_id=expected_revision_id,
    )
    warnings = _callout_emoji_warnings(doc, content or "")
    if warnings and isinstance(result, dict):
        result["format_warnings"] = warnings
    return result


@mcp.tool(title="Create a Lark document", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_document(
    content: str,
    doc_format: DocFormat = "markdown",
    parent_token: str | None = None,
) -> dict:
    """Create one Lark Docx in a Drive folder or the personal space root.

    Title: in Markdown the first line "# Title" becomes the document title; in XML use
    <title>Title</title>. parent_token is a Drive folder token only; omit it for the
    personal space root. For a Wiki page use create_wiki_node instead.

    Not idempotent: after an error or interruption, search_documents by the title
    before calling again, or a duplicate document is created.
    """
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    with _hidden_run() as run:
        payload = _payload(run / ".document", _render(content, doc_format, document_start=True))
        args = [
            "docs", "+create", "--api-version", "v2", "--as", "user",
            "--doc-format", doc_format, "--content", payload, "--format", "json",
        ]
        if parent_token:
            args.extend(["--parent-token", parent_token])
        result = _run_cli(args, "create_document")
        _assert_write_succeeded(result, "create_document")
    document = result.get("data", {}).get("document", {}) if isinstance(result, dict) else {}
    document_id = document.get("document_id") if isinstance(document, dict) else None
    if document_id:
        warnings = _callout_emoji_warnings(document_id, _render(content, doc_format, document_start=True))
        if warnings:
            result["format_warnings"] = warnings
    return result


@mcp.tool(title="Create a Lark Wiki node", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_wiki_node(
    title: str,
    parent_node_token: str | None = None,
    space_id: str | None = None,
) -> dict:
    """Create a blank Docx Wiki node in a specified Wiki space or beneath a parent node.

    Pass parent_node_token (Wiki node token) or space_id; one is required. The node is
    empty: write its body with batch_push using the returned Docx token or URL.
    """
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
    result = _run_cli(args, "create_wiki_node")
    _assert_write_succeeded(result, "create_wiki_node")
    return result


@mcp.tool(title="Create a Lark Wiki space", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_wiki_space(name: str, description: str | None = None) -> dict:
    """Create a Wiki space owned by the authorized Lark user."""
    if not name.strip():
        raise ValueError("name must not be empty")
    _check_lark_cli()
    args = ["wiki", "+space-create", "--as", "user", "--name", name, "--format", "json"]
    if description:
        args.extend(["--description", description])
    result = _run_cli(args, "create_wiki_space")
    _assert_write_succeeded(result, "create_wiki_space")
    return result


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
    after_block_id: str | None = None,
) -> dict:
    """Insert a base64 image or file, then delete its hidden local payload.

    media_type: "image" (default) or "file"; filename is a plain name with extension;
    decoded size at most 20 MB.

    lark-cli always appends media to the document end. Pass after_block_id (from
    find_document_text with doc_format="xml", detail="with-ids"; "0" = document start)
    to move the new block there afterwards.

    Not idempotent: each call uploads a new copy. If the move fails, the error is
    media_inserted_not_moved with media_block_id; the media is already at the end, so
    retry only block_update(command="block_move_after", src_block_ids=media_block_id).
    """
    _check_lark_cli()
    if media_type not in {"image", "file"}:
        raise ValueError("media_type must be image or file")
    if not filename or Path(filename).name != filename:
        raise ValueError("filename must be a plain file name without directories")
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
        result = _run_cli(args, "insert_media")
        _assert_write_succeeded(result, "insert_media")
    if not after_block_id:
        return result
    new_block = result.get("data", {}).get("block_id") if isinstance(result, dict) else None
    if not new_block:
        raise LarkCLIError({
            "operation": "insert_media",
            "error": "media_inserted_not_moved",
            "message": "media was appended to the document end but lark-cli returned no block_id to move",
            "result": result,
        })
    try:
        _run_block_command(
            doc, "block_move_after", block_id=after_block_id, src_block_ids=new_block,
            operation="insert_media move",
        )
    except LarkCLIError as error:
        # The upload succeeded; never let a caller retry and duplicate the media.
        error.details.update({
            "error": "media_inserted_not_moved",
            "media_block_id": new_block,
            "next_step": "Media is at the document end; retry only the move with block_update, not insert_media.",
        })
        raise
    return result


@mcp.tool(title="Read a Lark whiteboard", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def whiteboard_query(
    whiteboard_token: str, output_as: Literal["code", "raw"] = "code",
) -> dict:
    """Read one Lark whiteboard as Mermaid/PlantUML code or raw nodes.

    Get whiteboard_token from scan_document_assets. output_as: "code" (Mermaid/PlantUML
    source) or "raw" (node JSON).
    """
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
    """Update one Lark whiteboard from Mermaid, PlantUML, or raw node JSON.

    Get whiteboard_token from scan_document_assets. overwrite=True (default) deletes all
    existing whiteboard content first; pass False to add to it instead.
    """
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
        result = _run_cli(args, "whiteboard_update")
        _assert_write_succeeded(result, "whiteboard_update")
        return result


class SelectOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    hue: Literal["Red", "Orange", "Yellow", "Lime", "Green", "Turquoise", "Wathet", "Blue", "Carmine", "Purple", "Gray"] = "Blue"
    lightness: Literal["Lighter", "Light", "Standard", "Dark", "Darker"] = "Lighter"


# Table tools share the Docx authentication, subprocess and error envelope.
class BaseField(BaseModel):
    """CLI field definition; extra properties preserve advanced documented settings."""
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1)
    type: Literal[
        "text", "number", "select", "datetime", "created_at", "updated_at", "user",
        "group_chat", "created_by", "updated_by", "link", "formula", "lookup",
        "auto_number", "attachment", "location", "checkbox", "button",
    ]
    multiple: bool | None = None
    options: list[SelectOption] | None = None
    style: dict | None = None
    description: str | None = None


class SheetStyle(BaseModel):
    model_config = ConfigDict(extra="allow")
    background_color: str | None = None
    font_color: str | None = None
    font_size: float | None = None
    font_weight: Literal["normal", "bold"] | None = None
    font_style: Literal["normal", "italic"] | None = None
    font_line: Literal["none", "underline", "line-through"] | None = None
    horizontal_alignment: Literal["left", "center", "right"] | None = None
    vertical_alignment: Literal["top", "middle", "bottom"] | None = None
    word_wrap: Literal["overflow", "auto-wrap", "word-clip"] | None = None
    number_format: str | None = None


class SheetCell(BaseModel):
    """Only provided attributes are changed; use sheets_clear to clear content."""
    model_config = ConfigDict(extra="allow")
    value: str | int | float | bool | None = None
    formula: str | None = None
    cell_styles: SheetStyle | None = None
    note: str | None = None


# Linux caps one argv string at 128 KiB including NUL; keep room for the `--name=` prefix.
MAX_TABLE_JSON_BYTES = 120 * 1024


def _table_json_default(item: object):
    dump = getattr(item, "model_dump", None)
    if dump is None:
        raise ValueError(f"value of type {type(item).__name__} is not JSON-serializable; use plain strings, numbers, booleans, lists and objects")
    return dump(exclude_unset=True)


def _table_json(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, default=_table_json_default)
    if len(text.encode("utf-8")) > MAX_TABLE_JSON_BYTES:
        raise ValueError("table JSON exceeds 120 KiB; split it into smaller requests")
    return text


def _table_call(domain: str, command: str, options: dict, *, write: bool = False,
                confirmed: bool = False, dry_run: bool = False) -> dict:
    _check_lark_cli()
    args = [domain, "+" + command, "--as", "user", "--format", "json"]
    for key, value in options.items():
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            # Equals keeps user values beginning with '-' from becoming CLI flags.
            args.append(f"--{key}={str(item).lower() if isinstance(item, bool) else item}")
    if dry_run:
        args.append("--dry-run")
    if confirmed:
        args.append("--yes")
    result = _run_cli(args, f"{domain}_{command}")
    # A dry-run envelope is a preview, not a write result; only check the real write.
    if write and not dry_run:
        try:
            _assert_write_succeeded(result, f"{domain}_{command}")
        except LarkCLIError as error:
            error.details["next_step"] = (
                "The write may have partially applied. Inspect with sheets_read/sheets_info "
                "or base_inspect/base_query_records before retrying; do not blindly repeat creates."
            )
            error.details["response"] = result
            raise
    elif result.get("ok") is False:
        raise LarkCLIError({"operation": f"{domain}_{command}", "error": "remote_error", "result": result})
    return result


def _sheet_target(spreadsheet: str, sheet_id: str | None = None,
                  sheet_name: str | None = None, *, required: bool = True) -> dict:
    if not spreadsheet.strip():
        raise ValueError("spreadsheet URL or token is required")
    if bool(sheet_id) and bool(sheet_name) or required and not (sheet_id or sheet_name):
        raise ValueError("pass exactly one of sheet_id or sheet_name")
    return {"url" if "://" in spreadsheet else "spreadsheet-token": spreadsheet,
            "sheet-id": sheet_id, "sheet-name": sheet_name}


def _base_target(base: str, table_id: str | None = None) -> dict:
    if not base.strip():
        raise ValueError("base token or URL is required")
    if "://" in base:
        resolved = _table_call("base", "url-resolve", {"url": base})
        data = resolved.get("data", {})
        token = data.get("base_token")
        if not token:
            raise ValueError("URL did not resolve to base_token; pass the resolved Base token")
        base = token
    return {"base-token": base, "table-id": table_id}


@mcp.tool(title="Inspect a spreadsheet", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def sheets_info(spreadsheet: str) -> dict:
    """List all sub-sheets, IDs, names, dimensions and visibility. Accept URL or token.

    Read this before selecting a sheet; select by explicit name/ID, never tab index.
    """
    return _table_call("sheets", "workbook-info", _sheet_target(spreadsheet, required=False))


@mcp.tool(title="Create a spreadsheet", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def sheets_create(title: str, values: list[list[str | int | float | bool | None]] | None = None,
                  folder_token: str | None = None) -> dict:
    """Create a workbook, optionally with a 2D value array (header is first row).

    For exact formulas/types/styles use sheets_write afterwards. Creation is not
    idempotent: search_documents(doc_types="sheet") before retrying an uncertain result.
    """
    if not title.strip():
        raise ValueError("title is required")
    return _table_call("sheets", "workbook-create", {"title": title, "folder-token": folder_token,
                       "values": _table_json(values) if values is not None else None}, write=True)


@mcp.tool(title="Read spreadsheet cells", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def sheets_read(spreadsheet: str, cell_range: str, sheet_id: str | None = None,
                sheet_name: str | None = None,
                include: list[Literal["value", "formula", "style", "comment", "data_validation", "conditional_format", "truncation"]] = ["value"],
                max_chars: Annotated[int, Field(ge=1, le=500000)] = 25000) -> dict:
    """Read a bounded A1 range, e.g. A1:F30; select exactly one sheet ID/name.

    include controls returned categories. Inspect truncation/has_more and continue
    with smaller ranges when needed; a bounded response is not the whole dataset.
    """
    if not cell_range.strip() or not include:
        raise ValueError("cell_range and include must not be empty")
    return _table_call("sheets", "cells-get", {**_sheet_target(spreadsheet, sheet_id, sheet_name),
                       "range": cell_range, "include": ",".join(include), "max-chars": max_chars})


@mcp.tool(title="Write spreadsheet cells", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
def sheets_write(spreadsheet: str, cell_range: str, cells: Annotated[list[Annotated[list[SheetCell], Field(min_length=1)]], Field(min_length=1)],
                 sheet_id: str | None = None, sheet_name: str | None = None,
                 allow_overwrite: bool = False, copy_to_range: str | None = None,
                 dry_run: bool = False) -> dict:
    """Write a 2D array of cells: [[{"value":12},{"formula":"=A1*2"}]].

    cell_range is A1 anchor or bounding rectangle; formulas begin '='. Each cell can
    include cell_styles or note; omitted attributes remain unchanged. Overwriting
    nonempty cells requires allow_overwrite=True. copy_to_range fills a template
    with shifted formula references. Read back values/styles and run
    sheets_verify_formulas after formula writes. Dry run previews without writing.
    """
    if not cell_range.strip() or not cells or not cells[0] or any(len(row) != len(cells[0]) for row in cells):
        raise ValueError("cell_range and a nonempty rectangular cells array are required")
    return _table_call("sheets", "cells-set", {**_sheet_target(spreadsheet, sheet_id, sheet_name),
                       "range": cell_range, "cells": _table_json(cells),
                       "allow-overwrite": allow_overwrite, "copy-to-range": copy_to_range}, write=True, dry_run=dry_run)


@mcp.tool(title="Manage spreadsheet tabs", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def sheets_manage_sheet(spreadsheet: str, action: Literal["create", "rename", "copy", "delete", "hide", "unhide"],
                        sheet_id: str | None = None, sheet_name: str | None = None,
                        title: str | None = None, confirmed: bool = False, dry_run: bool = False) -> dict:
    """Manage one tab after sheets_info. create/rename require title; other actions target ID/name.

    delete requires confirmed=True after explicit user authorization. dry_run only
    previews. Existing tabs must only be renamed/deleted/hidden on user request.
    """
    if action not in {"create", "rename", "copy", "delete", "hide", "unhide"}:
        raise ValueError("unsupported sheet action")
    if action in {"create", "rename"} and not (title and title.strip()):
        raise ValueError("title is required for create/rename")
    if title is not None and action not in {"create", "rename", "copy"}:
        raise ValueError("title only applies to create/rename/copy")
    if action == "create" and (sheet_id or sheet_name):
        raise ValueError("create uses title, not a sheet selector")
    if action == "delete" and not (confirmed or dry_run):
        raise ValueError("delete requires confirmed=True or dry_run=True")
    return _table_call("sheets", "sheet-" + action,
                       {**_sheet_target(spreadsheet, sheet_id, sheet_name, required=action != "create"), "title": title},
                       write=True, confirmed=confirmed and action == "delete", dry_run=dry_run)


@mcp.tool(title="Clear spreadsheet cells", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
def sheets_clear(spreadsheet: str, cell_range: str, sheet_id: str | None = None,
                 sheet_name: str | None = None, scope: Literal["content", "formats", "all"] = "content",
                 confirmed: bool = False, dry_run: bool = False) -> dict:
    """Clear a bounded range, leaving rows/columns intact. Requires explicit user approval.

    content removes values, formats removes formatting, all removes both.
    Preview with dry_run=True; execute with confirmed=True.
    """
    if not cell_range.strip() or scope not in {"content", "formats", "all"}:
        raise ValueError("cell_range and a valid scope are required")
    if not (confirmed or dry_run):
        raise ValueError("clear requires confirmed=True or dry_run=True")
    return _table_call("sheets", "cells-clear", {**_sheet_target(spreadsheet, sheet_id, sheet_name),
                       "range": cell_range, "scope": scope}, write=True, confirmed=confirmed, dry_run=dry_run)


@mcp.tool(title="Verify spreadsheet formulas", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def sheets_verify_formulas(spreadsheet: str, cell_range: str, sheet_id: str | None = None,
                           sheet_name: str | None = None, ai_only: bool = False) -> dict:
    """Check all formulas/errors in the written range. partial means narrow and repeat.

    For AI formulas set ai_only=True; inspect failed count and report pending work.
    """
    if not cell_range.strip():
        raise ValueError("cell_range is required")
    return _table_call("sheets", "formula-verify", {**_sheet_target(spreadsheet, sheet_id, sheet_name),
                       "range": cell_range, "ai-only": ai_only})


@mcp.tool(title="Inspect a multidimensional Base", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def base_inspect(base: str, resource: Literal["base", "tables", "fields", "views"] = "tables",
                 table_id: str | None = None) -> dict:
    """Read Base metadata, tables, fields (including select options), or views.

    base accepts token or URL; Wiki URLs are resolved by CLI. table_id is an ID or
    exact table name, required for fields/views. Read fields before record writes.
    """
    commands = {"base": "base-get", "tables": "table-list", "fields": "field-list", "views": "view-list"}
    if resource not in commands or resource in {"fields", "views"} and not table_id:
        raise ValueError("valid resource and table_id for fields/views are required")
    if resource in {"base", "tables"} and table_id is not None:
        raise ValueError("table_id only applies to fields/views")
    return _table_call("base", commands[resource], _base_target(base, table_id))


@mcp.tool(title="Create a multidimensional Base", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def base_create(name: str, folder_token: str | None = None, time_zone: str = "Asia/Shanghai") -> dict:
    """Create a Base with its platform default first table. Configure fields afterwards.

    Not idempotent: search_documents(doc_types="bitable") before retrying an uncertain result.
    """
    if not name.strip():
        raise ValueError("name is required")
    return _table_call("base", "base-create", {"name": name, "folder-token": folder_token,
                       "time-zone": time_zone}, write=True)


@mcp.tool(title="Manage Base tables", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def base_manage_table(base: str, action: Literal["create", "rename", "delete"],
                      table_id: str | None = None, name: str | None = None,
                      fields: list[BaseField] | None = None, confirmed: bool = False,
                      dry_run: bool = False) -> dict:
    """Create (name+fields), rename (table_id+name), or delete (table_id) a table.

    First field is primary. select fields use options=[{"name":"Todo"}]. Delete
    requires explicit user approval and confirmed=True. dry_run previews only.
    """
    if action not in {"create", "rename", "delete"}:
        raise ValueError("unsupported table action")
    if action != "create" and not table_id or action != "delete" and not (name and name.strip()):
        raise ValueError("table_id/name required for the selected action")
    if action == "create" and (table_id or not fields):
        raise ValueError("create requires fields and no table_id")
    if action != "create" and fields is not None or action == "delete" and name is not None:
        raise ValueError("fields only applies to create; delete does not take name")
    if action == "delete" and not (confirmed or dry_run):
        raise ValueError("delete requires confirmed=True or dry_run=True")
    return _table_call("base", {"create": "table-create", "rename": "table-update", "delete": "table-delete"}[action],
                       {**_base_target(base, table_id), "name": name, "fields": _table_json(fields) if fields else None},
                       write=True, confirmed=confirmed and action == "delete", dry_run=dry_run)


@mcp.tool(title="Manage Base fields and options", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def base_manage_field(base: str, table_id: str, action: Literal["get", "create", "update", "delete"],
                      field_id: str | None = None, definition: BaseField | None = None,
                      confirmed: bool = False, dry_run: bool = False) -> dict:
    """Read/create/update/delete a field. Field types and select option colors have enums.

    get/update/delete require field_id (ID or name). create/update require definition.
    update is full PUT: read get first and preserve all writable properties, including
    options/style. update/delete require confirmed=True after user approval, or dry_run.
    Advanced documented fields (expression, link_table, etc.) are allowed in definition.
    """
    if not table_id or action not in {"get", "create", "update", "delete"}:
        raise ValueError("table_id and a valid action are required")
    if (action != "create") != bool(field_id):
        raise ValueError("field_id required except for create")
    if (action in {"create", "update"}) != (definition is not None):
        raise ValueError("definition required only for create/update")
    if action in {"update", "delete"} and not (confirmed or dry_run):
        raise ValueError("update/delete requires confirmed=True or dry_run=True")
    return _table_call("base", "field-" + action, {**_base_target(base, table_id), "field-id": field_id,
                       "json": _table_json(definition) if definition is not None else None},
                       write=action != "get", confirmed=confirmed and action in {"update", "delete"}, dry_run=dry_run)


@mcp.tool(title="Query Base records", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def base_query_records(base: str, table_id: str, record_ids: list[str] | None = None,
                       keyword: str | None = None, search_fields: list[str] | None = None,
                       fields: list[str] | None = None, view_id: str | None = None,
                       filter_config: dict | None = None, sort: list[dict] | None = None,
                       offset: Annotated[int, Field(ge=0)] = 0,
                       limit: Annotated[int, Field(ge=1, le=200)] = 100) -> dict:
    """Get known record IDs, keyword-search fields, or list/filter/sort records.

    filter_config example: {"logic":"and","conditions":[["Status","==","Todo"]]}.
    sort example: [{"field":"Updated","desc":true}]. fields projects names/IDs.
    Inline JSON uses offset/limit pagination: follow has_more and next_offset until
    complete. record_ids cannot combine with keyword/view/filter/sort/pagination.
    """
    if not table_id:
        raise ValueError("table_id is required")
    if record_ids is not None and (not record_ids or keyword is not None or search_fields or view_id or filter_config is not None or sort is not None or offset != 0 or limit != 100):
        raise ValueError("record_ids must be nonempty and cannot combine with query options")
    if keyword is not None and (not keyword.strip() or not search_fields) or search_fields and keyword is None:
        raise ValueError("keyword and search_fields must be provided together")
    options = {**_base_target(base, table_id), "field-id": fields}
    if record_ids is not None:
        command = "record-get"
        options["record-id"] = record_ids
    else:
        command = "record-search" if keyword is not None else "record-list"
        options.update({"keyword": keyword, "search-field": search_fields, "view-id": view_id,
                        "filter-json": _table_json(filter_config) if filter_config is not None else None,
                        "sort-json": _table_json(sort) if sort is not None else None, "offset": offset, "limit": limit})
    return _table_call("base", command, options)


@mcp.tool(title="Write Base records", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def base_write_records(base: str, table_id: str, action: Literal["create", "update", "delete"],
                       records: list[dict] | None = None, updates: dict[str, dict] | None = None,
                       record_ids: list[str] | None = None, confirmed: bool = False,
                       dry_run: bool = False) -> dict:
    """Batch create field maps, update record-ID -> field-map, or delete record IDs; 1-200.

    create: records=[{"Name":"A","Status":["Todo"]}]. update:
    updates={"rec_id":{"Status":["Done"]}}. null clears allowed cells. Read fields
    first; select values are arrays even for single-select. Do not write computed
    fields. Delete requires confirmed=True after user approval. dry_run previews.
    Creation is not idempotent; query after uncertain results before retrying.
    """
    if not table_id or action not in {"create", "update", "delete"}:
        raise ValueError("table_id and a valid action are required")
    selected = {"create": records, "update": updates, "delete": record_ids}[action]
    if sum(value is not None for value in (records, updates, record_ids)) != 1 or not selected or len(selected) > 200:
        raise ValueError("provide only the selected action's records/updates/record_ids, 1-200 items")
    if action == "delete" and not (confirmed or dry_run):
        raise ValueError("delete requires confirmed=True or dry_run=True")
    payload = {{"create": "create_records", "update": "update_records", "delete": "record_id_list"}[action]: selected}
    return _table_call("base", {"create": "record-batch-create", "update": "record-batch-update", "delete": "record-delete"}[action],
                       {**_base_target(base, table_id), "json": _table_json(payload)}, write=True,
                       confirmed=confirmed and action == "delete", dry_run=dry_run)


@mcp.tool(title="Manage Base views", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def base_manage_view(base: str, table_id: str,
                     action: Literal["get", "create", "rename", "delete", "set_filter", "set_sort", "set_group", "set_visible_fields"],
                     view_id: str | None = None, name: str | None = None,
                     view_type: Literal["grid", "kanban", "gallery", "calendar", "gantt"] = "grid",
                     config: dict | None = None, confirmed: bool = False, dry_run: bool = False) -> dict:
    """Manage views; create uses name+view_type, rename uses name; others need view_id.

    set_* requires config, e.g. set_filter {"logic":"and","conditions":[["Status","==","Todo"]]}.
    Configuration replaces that view setting; preserve existing desired criteria.
    Delete requires confirmed=True after explicit user approval, or dry_run to preview.
    """
    actions = {"get", "create", "rename", "delete", "set_filter", "set_sort", "set_group", "set_visible_fields"}
    if not table_id or action not in actions:
        raise ValueError("table_id and a valid action are required")
    if (action != "create") != bool(view_id):
        raise ValueError("view_id required except for create")
    if (action in {"create", "rename"}) != bool(name and name.strip()):
        raise ValueError("name required only for create/rename")
    if action.startswith("set_") != (config is not None):
        raise ValueError("config required only for set_* actions")
    if action == "delete" and not (confirmed or dry_run):
        raise ValueError("delete requires confirmed=True or dry_run=True")
    payload = {"name": name, "type": view_type} if action == "create" else config
    return _table_call("base", "view-" + action.replace("_", "-"),
                       {**_base_target(base, table_id), "view-id": view_id,
                        "name": name if action == "rename" else None,
                        "json": _table_json(payload) if payload is not None else None},
                       write=action != "get", confirmed=confirmed and action == "delete", dry_run=dry_run)


@mcp.tool(title="Schedule lark-doc MCP restart", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False})
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
        "service": "lark-doc-mcp.service",
        "delay_seconds": delay_seconds,
        "worker_pid": worker.pid,
        "message": "The service will restart after the delay; the current tool call is complete.",
    }


# Late callbacks retain the server's shared authentication and testable CLI runner.
from sheets_tools import register_sheets_tools

register_sheets_tools(
    mcp, TOOL_META,
    lambda command, options, **kwargs: _table_call("sheets", command, options, **kwargs),
    _sheet_target, _table_json,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument(
        "--allow-unauthenticated", action="store_true",
        help="serve HTTP without authentication; local development only",
    )
    args = parser.parse_args()
    _cleanup_stale_runs()
    if args.transport == "http":
        uvicorn_config = _https_config(
            args.host, args.tls_cert, args.tls_key, args.allow_unauthenticated,
        )
        mcp.run(
            transport="http", host=args.host, port=args.port,
            uvicorn_config=uvicorn_config,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
