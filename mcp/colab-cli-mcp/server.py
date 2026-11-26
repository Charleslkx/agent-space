#!/usr/bin/env python3
"""Expose selected Google Colab CLI commands over authenticated Streamable HTTP."""
from __future__ import annotations

import argparse
from functools import partial
import json
import os
from typing import Annotated
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware
from starlette.routing import Route
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from pydantic import Field

from cli import call_cli

BASE_URL_ENV = "COLAB_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "COLAB_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "COLAB_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "COLAB_MCP_GITHUB_USERS"
JWT_SIGNING_KEY_ENV = "COLAB_MCP_JWT_SIGNING_KEY"
STORAGE_KEY_ENV = "COLAB_MCP_STORAGE_KEY"
REDIS_PASSWORD_ENV = "COLAB_MCP_REDIS_PASSWORD"
REDIS_HOST_ENV = "COLAB_MCP_REDIS_HOST"

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Acolab-cli-mcp/oauth/callback"
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
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.path or parsed.query or parsed.fragment
        or any(char.isspace() for char in base_url)
    ):
        raise RuntimeError(f"{BASE_URL_ENV} must be an HTTPS origin without a path")
    return base_url


def _auth_provider(base_url: str) -> GitHubProvider:
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
        base_url=base_url,
        resource_base_url=base_url,
        required_scopes=["read:user"],
        allowed_client_redirect_uris=ALLOWED_CLIENT_REDIRECT_URIS,
    )


def _authorized_github_user(ctx: AuthContext, users: frozenset[str]) -> bool:
    login = ((ctx.token.claims or {}).get("login") if ctx.token else "") or ""
    return isinstance(login, str) and login.casefold() in users


def create_server() -> FastMCP:
    """Build the MCP server after validating the deployment configuration."""
    users = _github_users()
    base_url = _base_url()
    server = FastMCP(
        name="Colab-CLI",
        version="0.1.0",
        instructions=(
            "Use colab_cli with the subcommand first. Execute Python in Google Colab, "
            "not on the MCP host. The deployment has one shared Google account and session state. "
            "Use explicit session names and check status before retrying timed-out calls."
        ),
        website_url=base_url,
        auth=_auth_provider(base_url),
        middleware=[AuthMiddleware(auth=partial(_authorized_github_user, users=users))],
    )

    @server.tool(
        title="Run Google Colab CLI",
        meta={"securitySchemes": [{"type": "oauth2", "scopes": ["read:user"]}]},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    def colab_cli(
        args: Annotated[list[str], Field(
            description=(
                "Colab argument tokens, starting with a supported subcommand. "
                "Pass only the tokens after the colab executable. Each flag, value and "
                "operand is a separate string, or use --flag=value. Use -- before "
                "operands starting with a dash. Each token is at most 4096 UTF-8 bytes "
                "with no ASCII control characters. The tool description lists supported options."
            ),
            min_length=1,
            max_length=128,
            examples=[["new", "-s", "demo"], ["exec", "-s", "demo"], ["version"]],
        )],
        stdin: Annotated[str | None, Field(
            description=(
                "Required for exec without -f/--file. Send nonblank Python source as UTF-8 "
                "text, up to 1 MiB by bytes. It runs in the selected Colab kernel. "
                "Omit or use null for every other invocation."
            ),
            examples=["print(2 + 2)"],
        )] = None,
        file_base64: Annotated[str | None, Field(
            description=(
                "Required for upload, run and exec -f/--file. Send the actual file bytes "
                "as standard base64 without whitespace or a data URL prefix, up to 4 MiB "
                "after decoding. The plain filename goes in args. "
                "Omit or use null for every other invocation."
            ),
            examples=["cHJpbnQoNCkK"],
        )] = None,
    ) -> dict:
        """Run Google Colab commands under the deployment's shared Google account.

        Put the subcommand first in args. Brackets below mark optional arguments.
        Use ["COMMAND", "--help"] or ["COMMAND", "-h"] for installed CLI help.
        The supported MCP options are listed here because CLI help can show extra options.

        Runtime management
        - `new [-s NAME] [--gpu MODEL | --tpu MODEL] [--high-mem]` allocates a runtime.
        - `sessions` lists active backend runtimes for the account.
        - `status [-s NAME]` shows tracked runtime status and hardware. With no name it shows all
          tracked runtimes.
        - `restart-kernel [-s NAME]` resets the selected kernel and clears its in-memory variables.
        - `stop [-s NAME]` releases the runtime. Retrieve needed files first.
        - `url [-s NAME]` returns a browser link for the selected runtime.

        Execution and dependencies
        - `exec [-s NAME] [--timeout SECONDS] [-f FILE]` uses an existing runtime. Send stdin for
          inline Python or file_base64 with -f for a .py or .ipynb. Notebook output uses the name
          STEM_output.ipynb.
        - `run [-s NAME] [--gpu MODEL | --tpu MODEL] [--high-mem] [--keep] [--timeout SECONDS] SCRIPT.py [-- ARGS...]` runs
          a supplied script on a fresh runtime. Send file_base64. Put -- before script arguments
          that start with a dash. The CLI attempts to release the runtime when the script finishes.
          Use --keep to retain it for subsequent calls.
        - `install [-s NAME] PACKAGE [PACKAGE...]` installs packages in the selected runtime. Use
          names, extras or version specifiers, such as numpy and pandas>=2. Requirements files, URLs
          and pip flags are outside this interface.

        Files and history
        - `ls [-s NAME] [REMOTE]` lists a remote path. The default is /content.
        - `rm [-s NAME] REMOTE` requests deletion of a remote entry. This changes runtime files.
        - `upload [-s NAME] FILE REMOTE` writes supplied file_base64 to the remote destination and
          can overwrite a file.
        - `download [-s NAME] REMOTE FILE` returns a remote file as file.name and file.base64.
        - `log [-s NAME] [-n N] [-t TYPE] [-o FILE]` inspects local CLI history. With no session it
          lists names with history. Use -n for the last 1–9999 entries or -t for a type such as
          execution or file_operation. Export requires -s and an .ipynb, .md, .txt or .jsonl
          filename.
        - `usage` shows shared account compute-unit usage and balance.
        - `version` shows the installed Google Colab CLI version.

        Parameters and defaults
        - -s is --session. Names are case-sensitive. Use 1–64 ASCII letters, digits, hyphens or
          underscores, starting with a letter or digit. Use explicit names across calls. New and run
          generate a name when omitted and reject a name already recorded locally. Commands
          targeting one existing runtime can infer it when exactly one is tracked and active.
        - New and run default to CPU. Use at most one accelerator flag. CLI 0.8.0 accepts T4, L4,
          G4, H100 and A100 for --gpu and v5e1 and v6e1 for --tpu, case-insensitively. Check
          installed CLI help for other versions. Availability and --high-mem depend on the Google
          account and hardware. Check the resulting shape with status.
        - --timeout is the CLI's wait for output during execution. It defaults to 30 seconds and
          accepts whole or fractional values from 0 to 240, with 0 disabling that inactivity limit.
          Every MCP invocation still has a 240-second overall deadline.
        - -f is --file. For log, -n is --lines, -t is --type and -o is --output.
        - A FILE or SCRIPT filename uses 1–128 ASCII letters, digits, dots, hyphens or underscores
          and starts with a letter or digit. Remote paths refer to Colab, for example
          /content/results.csv. Use whole paths as single tokens and omit dot or parent components
          and backslashes.

        Tool argument examples
        - {"args":["exec","-s","demo"],"stdin":"print(2 + 2)"}
        - {"args":["upload","-s","demo","sample.py","/content/sample.py"],"file_base64":"cHJpbnQoNCkK"}
        - {"args":["download","-s","demo","/content/sample.py","sample.py"]}

        Results include exit_code, timed_out, stdout and stderr. A kernel error can appear in output
        even if exit_code is 0. When a stream exceeds 256 KiB, truncated is true and original_bytes
        reports observed bytes for both streams. Decode file.base64 to retrieve an artifact of at
        most 4 MiB. If the CLI exits successfully but the artifact is missing or exceeds the limit,
        read file_error.

        Validation and capacity failures are MCP tool errors. A busy error means no CLI command
        started. On timeout exit_code is null. The remote code or allocation may continue, so check
        sessions and status before retrying. OAuth, server config, interactive terminals, SSH and
        Drive mounting are outside the tool interface.
        """
        return call_cli(args, stdin, file_base64)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    create_server().run(
        transport="streamable-http", host=options.host, port=options.port, path="/mcp",
        stateless_http=True, show_banner=False, uvicorn_config={"access_log": False},
    )


if __name__ == "__main__":
    main()
