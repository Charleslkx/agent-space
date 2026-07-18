#!/usr/bin/env python3
"""Small FastMCP wrapper around lark-cli document and whiteboard operations."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import argparse
import ipaddress
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastmcp import FastMCP

TOKEN_ENV = "LARK_MCP_AUTH_TOKEN"


def _auth_provider():
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return None
    if len(token) < 32:
        raise RuntimeError(f"{TOKEN_ENV} must contain at least 32 characters")
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(tokens={token: {
        "client_id": "personal-lark-mcp",
        "scopes": ["lark:read", "lark:write"],
    }})


mcp = FastMCP(name="Lark Obsidian Publish", auth=_auth_provider())
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
    token = os.environ.get(TOKEN_ENV)
    if bool(cert) != bool(key):
        raise RuntimeError("--tls-cert and --tls-key must be supplied together")
    if not _is_loopback(host) and not token:
        raise RuntimeError(f"public HTTP binding requires {TOKEN_ENV}")
    if not _is_loopback(host) and not cert:
        raise RuntimeError("public HTTP binding requires --tls-cert and --tls-key")
    if cert:
        if not token:
            raise RuntimeError(f"HTTPS mode requires {TOKEN_ENV}")
        if not cert.is_file() or not key or not key.is_file():
            raise RuntimeError("TLS certificate or key file does not exist")
        return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    return {}


def _run_cli(args: list[str]) -> dict:
    result = subprocess.run(
        ["lark-cli", *args],
        text=True,
        capture_output=True,
        env={**os.environ, **QUIET_ENV},
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"lark-cli returned invalid JSON: {error}") from error


def _check_lark_cli(use_cache: bool = True) -> dict:
    global _auth_cache
    if use_cache and _auth_cache and time.monotonic() < _auth_cache[0]:
        return _auth_cache[1]
    executable = shutil.which("lark-cli")
    if not executable:
        raise RuntimeError("lark-cli is not installed or not on PATH")
    version = subprocess.run(
        [executable, "--version"], text=True, capture_output=True, check=True
    ).stdout.strip()
    for attempt in range(2):
        auth = _run_cli(["auth", "status", "--json", "--verify"])
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
        shutil.rmtree(path, ignore_errors=True)
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
    path.write_text(content, encoding="utf-8")
    return "@" + path.relative_to(Path.cwd()).as_posix()


@mcp.tool
def check_lark_cli() -> dict:
    """Check the lark-cli binary, version, and verified user login."""
    return _check_lark_cli(use_cache=False)


@mcp.tool
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
    pulled = []
    for doc in documents:
        result = _run_cli([
            "docs", "+fetch", "--api-version", "v2", "--as", "user",
            "--doc", doc, "--doc-format", doc_format, "--detail", detail,
            "--format", "json",
        ])
        document = result["data"]["document"]
        pulled.append({
            "doc": doc,
            "revision_id": document.get("revision_id"),
            "content": document.get("content", ""),
        })
    return pulled


@mcp.tool
def batch_push(documents: list[dict[str, str]]) -> list[dict]:
    """Overwrite or append multiple documents from inline content."""
    _check_lark_cli()
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
            result = _run_cli([
                "docs", "+update", "--api-version", "v2", "--as", "user",
                "--doc", item["doc"], "--command", mode,
                "--doc-format", doc_format, "--content", content, "--format", "json",
            ])
            results.append({"doc": item["doc"], "result": result})
    return results


@mcp.tool
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
        ])


@mcp.tool
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
            return _run_cli(args)
        except RuntimeError as error:
            if "4003101" not in str(error) or attempt == 4:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


@mcp.tool
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
        return _run_cli(args)


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
