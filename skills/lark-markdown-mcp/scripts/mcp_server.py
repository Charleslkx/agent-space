#!/usr/bin/env python3
"""Small FastMCP wrapper around lark-cli document and whiteboard operations."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import shutil
import subprocess
import tempfile
import ipaddress
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.middleware import AuthMiddleware

TOKEN_ENV = "LARK_MCP_AUTH_TOKEN"
AUTH_MODE_ENV = "LARK_MCP_AUTH_MODE"
BASE_URL_ENV = "LARK_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "LARK_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "LARK_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USER_ENV = "LARK_MCP_GITHUB_USER"
GITHUB_JWT_SIGNING_KEY_ENV = "LARK_MCP_JWT_SIGNING_KEY"
CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
CLI_TIMEOUT_SECONDS = 60
MAX_BATCH_ITEMS = 100
MAX_CONTENT_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024


def _auth_mode() -> str:
    mode = os.environ.get(AUTH_MODE_ENV)
    if mode:
        if mode not in {"none", "token", "github"}:
            raise RuntimeError(f"{AUTH_MODE_ENV} must be none, token, or github")
        return mode
    return "token" if os.environ.get(TOKEN_ENV) else "none"


AUTH_MODE = _auth_mode()


class _ChatGPTOriginCompatibleGitHubProvider(GitHubProvider):
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
            GITHUB_CLIENT_SECRET_ENV, GITHUB_USER_ENV, GITHUB_JWT_SIGNING_KEY_ENV,
        )
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"GitHub OAuth requires: {', '.join(missing)}")
        base_url = os.environ[BASE_URL_ENV].rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path:
            raise RuntimeError(f"{BASE_URL_ENV} must be an HTTPS origin without a path")
        return _ChatGPTOriginCompatibleGitHubProvider(
            client_id=os.environ[GITHUB_CLIENT_ID_ENV],
            client_secret=os.environ[GITHUB_CLIENT_SECRET_ENV],
            jwt_signing_key=os.environ[GITHUB_JWT_SIGNING_KEY_ENV],
            base_url=base_url,
            resource_base_url=base_url,
            required_scopes=["read:user"],
            allowed_client_redirect_uris=[
                "https://chatgpt.com/connector/oauth/*",
                "https://chatgpt.com/connector_platform_oauth_redirect",
                "http://localhost:*",
                "http://127.0.0.1:*",
            ],
        )
    if mode == "none":
        return None
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"token auth requires {TOKEN_ENV}")
    if len(token) < 32:
        raise RuntimeError(f"{TOKEN_ENV} must contain at least 32 characters")
    from fastmcp.server.auth import StaticTokenVerifier

    return StaticTokenVerifier(tokens={token: {
        "client_id": "personal-lark-mcp",
        "scopes": ["lark:read", "lark:write"],
    }})


def _authorized_github_user(ctx: AuthContext) -> bool:
    expected = os.environ.get(GITHUB_USER_ENV)
    return bool(
        ctx.token and expected
        and (ctx.token.claims or {}).get("login", "").casefold() == expected.casefold()
    )


def _security_schemes(mode: str) -> list[dict]:
    if mode == "github":
        return [{"type": "oauth2", "scopes": ["read:user"]}]
    if mode == "none":
        return [{"type": "noauth"}]
    return []


TOOL_META = {"securitySchemes": _security_schemes(AUTH_MODE)}
AUTH_PROVIDER = _auth_provider()
mcp = FastMCP(
    name="Lark Markdown MCP",
    version="0.7.0",
    instructions=(
        "Use batch_pull before modifying an unfamiliar document. "
        "Use point_update for one exact change and batch_push for full replacements. "
        "Write tools modify the authenticated user's Lark documents."
    ),
    website_url=os.environ.get(BASE_URL_ENV),
    auth=AUTH_PROVIDER,
    middleware=(
        [AuthMiddleware(auth=_authorized_github_user)]
        if AUTH_MODE == "github" else []
    ),
)
WORKDIR = Path(".lark_publish")
QUIET_ENV = {
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}
AUTH_CACHE_SECONDS = 60
_auth_cache: tuple[float, dict] | None = None


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


def _run_cli(args: list[str], context: str = "lark-cli") -> dict:
    try:
        result = subprocess.run(
            ["lark-cli", *args],
            text=True,
            capture_output=True,
            env={**os.environ, **QUIET_ENV},
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(json.dumps({
            "operation": context,
            "error": "timeout",
            "timeout_seconds": CLI_TIMEOUT_SECONDS,
            "next_step": "retry once; if it repeats, run lark-cli auth status --json --verify",
        })) from error
    if result.returncode:
        raise RuntimeError(json.dumps({
            "operation": context,
            "error": "lark_cli_failed",
            "exit_code": result.returncode,
            "message": (result.stderr.strip() or result.stdout.strip())[:4000],
        }, ensure_ascii=False))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(json.dumps({
            "operation": context,
            "error": "invalid_json",
            "message": str(error),
        })) from error


def _check_lark_cli(use_cache: bool = True) -> dict:
    global _auth_cache
    if use_cache and _auth_cache and time.monotonic() < _auth_cache[0]:
        return _auth_cache[1]
    executable = shutil.which("lark-cli")
    if not executable:
        raise RuntimeError("lark-cli is not installed or not on PATH")
    try:
        version = subprocess.run(
            [executable, "--version"], text=True, capture_output=True,
            check=True, timeout=CLI_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"unable to run lark-cli --version: {error}") from error
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
        raise RuntimeError("lark-cli user authentication is unavailable after one retry")
    status = {
        "executable": executable,
        "version": version,
        "identity": auth.get("identity"),
        "user_status": user.get("status"),
        "verified": True,
    }
    _auth_cache = (time.monotonic() + AUTH_CACHE_SECONDS, status)
    return status


@contextmanager
def _hidden_run() -> Iterator[Path]:
    if WORKDIR.is_symlink():
        raise RuntimeError("refusing to use a symlinked .lark_publish directory")
    WORKDIR.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=".run-", dir=WORKDIR))
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path)
        except OSError as error:
            raise RuntimeError(
                f"failed to remove temporary payload {path.name}: {error}"
            ) from error
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
    return "@" + path.resolve().relative_to(Path.cwd()).as_posix()


def _validate_batch(items: list, name: str) -> None:
    if not items:
        raise ValueError(f"{name} must contain at least one item")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"{name} exceeds {MAX_BATCH_ITEMS} items")


def _batch_failure(operation: str, index: int, item: str, completed: int, error: Exception) -> RuntimeError:
    return RuntimeError(json.dumps({
        "operation": operation,
        "failed_index": index,
        "failed_item": item,
        "completed": completed,
        "error": str(error),
    }, ensure_ascii=False))


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
        result = subprocess.run(
            [
                "lark-cli", "auth", "qrcode", verification_url,
                "--output", output.relative_to(Path.cwd()).as_posix(),
            ],
            text=True,
            capture_output=True,
            env={**os.environ, **QUIET_ENV},
            timeout=CLI_TIMEOUT_SECONDS,
        )
        if result.returncode:
            raise RuntimeError(json.dumps({
                "operation": "generate lark authorization QR code",
                "error": "lark_cli_failed",
                "exit_code": result.returncode,
                "message": (result.stderr.strip() or result.stdout.strip())[:4000],
            }, ensure_ascii=False))
        return base64.b64encode(output.read_bytes()).decode("ascii")


@mcp.tool(title="Start Lark user authorization", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def begin_lark_auth() -> dict:
    """Create a one-time Lark device authorization URL and QR code for Docs and Drive."""
    payload = _run_cli(
        ["auth", "login", "--domain", "docs", "--domain", "drive", "--no-wait", "--json"],
        "start lark user authorization",
    )
    verification_url = _find_auth_value(
        payload, ("verification_url", "verification_uri_complete"),
    )
    device_code = _find_auth_value(payload, ("device_code",))
    if not verification_url or not device_code:
        raise RuntimeError("lark-cli did not return a verification URL and device code")
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


@mcp.tool(title="Check Lark CLI", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def check_lark_cli() -> dict:
    """Check the lark-cli binary, version, and verified user login."""
    return _check_lark_cli(use_cache=False)


@mcp.tool(title="Batch pull Lark documents", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def batch_pull(
    documents: list[str],
    doc_format: str = "markdown",
    detail: str = "simple",
) -> list[dict]:
    """Fetch multiple Lark Docx or Wiki documents as Markdown or XML."""
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    if detail not in {"simple", "with-ids", "full"}:
        raise ValueError("detail must be simple, with-ids, or full")
    _validate_batch(documents, "documents")
    pulled = []
    for index, doc in enumerate(documents):
        try:
            result = _run_cli([
                "docs", "+fetch", "--api-version", "v2", "--as", "user",
                "--doc", doc, "--doc-format", doc_format, "--detail", detail,
                "--format", "json",
            ], f"batch_pull documents[{index}]")
        except RuntimeError as error:
            raise _batch_failure("batch_pull", index, doc, len(pulled), error) from error
        document = result["data"]["document"]
        pulled.append({
            "doc": doc,
            "revision_id": document.get("revision_id"),
            "content": document.get("content", ""),
        })
    return pulled


@mcp.tool(title="Batch push Lark documents", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def batch_push(documents: list[dict[str, str]]) -> list[dict]:
    """Overwrite or append multiple documents from inline content."""
    _check_lark_cli()
    _validate_batch(documents, "documents")
    results = []
    with _hidden_run() as run:
        for index, item in enumerate(documents):
            mode = item.get("mode", "overwrite")
            doc_format = item.get("doc_format", "markdown")
            if mode not in {"overwrite", "append"}:
                raise ValueError("mode must be overwrite or append")
            if doc_format not in {"markdown", "xml"}:
                raise ValueError("doc_format must be markdown or xml")
            content = _payload(run / f".{index}.content", item["content"])
            try:
                result = _run_cli([
                    "docs", "+update", "--api-version", "v2", "--as", "user",
                    "--doc", item["doc"], "--command", mode,
                    "--doc-format", doc_format, "--content", content, "--format", "json",
                ], f"batch_push documents[{index}]")
            except RuntimeError as error:
                raise _batch_failure(
                    "batch_push", index, item.get("doc", ""), len(results), error
                ) from error
            results.append({"doc": item["doc"], "result": result})
    return results


@mcp.tool(title="Update exact document text", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
def point_update(
    doc: str,
    pattern: str,
    replacement: str,
    doc_format: str = "markdown",
) -> dict:
    """Replace one exact text target and remove the hidden payload afterward."""
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    with _hidden_run() as run:
        content = _payload(run / ".replacement", replacement)
        return _run_cli([
            "docs", "+update", "--api-version", "v2", "--as", "user",
            "--doc", doc, "--command", "str_replace", "--pattern", pattern,
            "--doc-format", doc_format, "--content", content, "--format", "json",
        ], "point_update")


@mcp.tool(title="Create a Lark document", meta=TOOL_META, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
def create_document(
    content: str,
    doc_format: str = "markdown",
    parent_token: str | None = None,
) -> dict:
    """Create one Lark Docx in a Drive folder, Wiki node, or personal space."""
    _check_lark_cli()
    if doc_format not in {"markdown", "xml"}:
        raise ValueError("doc_format must be markdown or xml")
    with _hidden_run() as run:
        payload = _payload(run / ".document", content)
        args = [
            "docs", "+create", "--api-version", "v2", "--as", "user",
            "--doc-format", doc_format, "--content", payload, "--format", "json",
        ]
        if parent_token:
            args.extend(["--parent-token", parent_token])
        return _run_cli(args, "create_document")


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
            "--file", path.relative_to(Path.cwd()).as_posix(),
            "--type", media_type, "--format", "json",
        ]
        if selection:
            args.extend(["--selection-with-ellipsis", selection])
        if before:
            args.append("--before")
        return _run_cli(args, "insert_media")


@mcp.tool(title="Read a Lark whiteboard", meta=TOOL_META, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def whiteboard_query(whiteboard_token: str, output_as: str = "code") -> dict:
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
    input_format: str = "mermaid",
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
