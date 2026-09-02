#!/usr/bin/env python3
"""OAuth-protected Streamable HTTP passthrough for Exa's hosted MCP server.

Unlike the sibling services in this repository, this one wraps no CLI binary:
Exa ships no CLI, and its official MCP server is itself a remote HTTP service.
So this is a proxy. Every tool is fetched from https://mcp.exa.ai/mcp at request
time and forwarded verbatim -- names, descriptions, and schemas are never
redeclared here, which is what keeps "usage identical to the official MCP" true
even after Exa changes its API.

What this deployment adds on top of the upstream server:
  * The Exa API key lives here, server-side, so no client ever needs one and
    none of them hit the anonymous free tier's rate limits.
  * Inbound access is gated on GitHub OAuth plus a login allowlist, or on a
    static bearer token for clients that cannot perform the OAuth dance.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import re
from urllib.parse import urlparse

import anyio
from cryptography.fernet import Fernet
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth import AccessToken, AuthContext, MultiAuth, TokenVerifier
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware, CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

LOGGER = logging.getLogger("exa_mcp")

BASE_URL_ENV = "EXA_MCP_BASE_URL"
GITHUB_CLIENT_ID_ENV = "EXA_MCP_GITHUB_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "EXA_MCP_GITHUB_CLIENT_SECRET"
GITHUB_USERS_ENV = "EXA_MCP_GITHUB_USERS"
JWT_SIGNING_KEY_ENV = "EXA_MCP_JWT_SIGNING_KEY"
STORAGE_KEY_ENV = "EXA_MCP_STORAGE_KEY"
REDIS_PASSWORD_ENV = "EXA_MCP_REDIS_PASSWORD"
REDIS_HOST_ENV = "EXA_MCP_REDIS_HOST"
STATIC_TOKENS_ENV = "EXA_MCP_STATIC_TOKENS"
UPSTREAM_URL_ENV = "EXA_MCP_UPSTREAM_URL"
UPSTREAM_TOOLS_ENV = "EXA_MCP_UPSTREAM_TOOLS"
BOOT_PROBE_ENV = "EXA_MCP_BOOT_PROBE"
API_KEY_ENV = "EXA_API_KEY"

SCOPES = ["read:user"]

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_REDIRECT_URI_PATTERN = "http://localhost:*"
WORKBUDDY_REDIRECT_URI = "workbuddy://workbuddy/mcp/custom-mcp%3Aexa/oauth/callback"
GROK_REDIRECT_URI = "https://grok.com/connectors-oauth-exchange-code/"
CURSOR_REDIRECT_URI = "https://www.cursor.com/agents/mcp/oauth/callback"
ALLOWED_CLIENT_REDIRECT_URIS = [
    "https://chatgpt.com/connector/oauth/*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    GROK_REDIRECT_URI,
    CURSOR_REDIRECT_URI,
    WORKBUDDY_REDIRECT_URI,
    # Covers the loopback listeners used by Claude Code, Codex, VS Code, Zed,
    # Gemini CLI, and every other client that runs the flow on localhost.
    "http://localhost:*",
    "http://127.0.0.1:*",
]

DEFAULT_UPSTREAM_URL = "https://mcp.exa.ai/mcp"
# The upstream server only enables web_search_exa and web_fetch_exa unless the
# rest are named explicitly, so this list is what makes the proxy expose Exa's
# full tool surface rather than just the two defaults.
DEFAULT_UPSTREAM_TOOLS = "web_search_exa,web_fetch_exa,agent_run,web_search_advanced_exa"
_TOOLS_PATTERN = re.compile(r"[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*")

# agent_run holds its call open while the whole agent loop runs, and upstream
# only gives up at ~750s. The read timeout has to clear that, or long runs die
# here instead of returning the run id the client needs to resume them.
UPSTREAM_TIMEOUT_SECONDS = 800

# Caps concurrent upstream calls, not inbound requests: callers queue here
# rather than being refused, which keeps a burst from one client from eating
# the whole account's rate limit.
MAX_CONCURRENT_CALLS = max(1, int(os.environ.get("EXA_MCP_MAX_CONCURRENCY", "8")))
ACQUIRE_TIMEOUT_SECONDS = 5.0

# Short enough that a broken key surfaces as a failed boot, long enough not to
# trip on a slow cold start.
BOOT_PROBE_TIMEOUT_SECONDS = 20.0
MIN_STATIC_TOKEN_LENGTH = 32


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


class StaticTokenVerifier(TokenVerifier):
    """Bearer-token channel for clients that cannot run the OAuth flow.

    Trae's MCP client never opens a browser and never refreshes a token -- its
    only option for an authenticated HTTP server is a hardcoded header -- so an
    OAuth-only deployment locks it out no matter which redirect URIs are
    allowlisted. Each token carries the GitHub login it stands in for, so
    AuthMiddleware's allowlist check stays a single code path for both channels.
    """

    def __init__(self, tokens: dict[str, str]):
        super().__init__()
        self._tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        login: str | None = None
        # Every entry is compared, and compared in constant time, so that a
        # wrong token costs the same no matter which prefix it happens to share
        # with a real one.
        for candidate, candidate_login in self._tokens.items():
            if hmac.compare_digest(token, candidate):
                login = candidate_login
        if login is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"static-token:{login}",
            scopes=list(SCOPES),
            expires_at=None,
            claims={"login": login, "auth_channel": "static_token"},
        )


class ConcurrencyMiddleware(Middleware):
    """Bound the number of tool calls in flight against the upstream server."""

    def __init__(self, limit: int = MAX_CONCURRENT_CALLS):
        self._limit = limit
        self._limiter: anyio.Semaphore | None = None

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        # Built on first use, not in __init__: anyio primitives belong to the
        # event loop that runs them, and this object is constructed at import.
        if self._limiter is None:
            self._limiter = anyio.Semaphore(self._limit)
        try:
            with anyio.fail_after(ACQUIRE_TIMEOUT_SECONDS):
                await self._limiter.acquire()
        except TimeoutError:
            raise RuntimeError(
                f"server is at capacity ({self._limit} concurrent Exa calls); "
                "nothing ran, retry in a few seconds"
            ) from None
        try:
            return await call_next(context)
        finally:
            self._limiter.release()


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


def _static_tokens() -> dict[str, str]:
    """Parse `login:token` pairs into a token -> login map."""
    raw = os.environ.get(STATIC_TOKENS_ENV, "").strip()
    if not raw:
        return {}
    allowed_logins = _github_users()
    tokens: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        login, separator, token = entry.partition(":")
        login = login.strip().casefold()
        token = token.strip()
        if not separator or not login or not token:
            raise RuntimeError(f"{STATIC_TOKENS_ENV} entries must be formatted as 'github-login:token'")
        if len(token) < MIN_STATIC_TOKEN_LENGTH:
            raise RuntimeError(
                f"{STATIC_TOKENS_ENV} token for {login!r} is shorter than "
                f"{MIN_STATIC_TOKEN_LENGTH} characters; generate one with `openssl rand -hex 32`"
            )
        # A token for a login outside the allowlist authenticates but then has
        # every tool filtered away by AuthMiddleware, which looks like a broken
        # server rather than a misconfiguration. Refuse it at boot instead.
        if login not in allowed_logins:
            raise RuntimeError(
                f"{STATIC_TOKENS_ENV} maps a token to {login!r}, which is not in {GITHUB_USERS_ENV}"
            )
        if token in tokens:
            raise RuntimeError(f"{STATIC_TOKENS_ENV} reuses the same token for more than one login")
        tokens[token] = login
    return tokens


def _auth_provider():
    storage_key = _required(STORAGE_KEY_ENV).encode()
    try:
        fernet = Fernet(storage_key)
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"{STORAGE_KEY_ENV} must be a valid Fernet key") from error
    redis = RedisStore(
        host=os.environ.get(REDIS_HOST_ENV, "redis"),
        password=_required(REDIS_PASSWORD_ENV),
    )
    github = _OriginCompatibleGitHubProvider(
        client_id=_required(GITHUB_CLIENT_ID_ENV),
        client_secret=_required(GITHUB_CLIENT_SECRET_ENV),
        jwt_signing_key=_required(JWT_SIGNING_KEY_ENV),
        client_storage=FernetEncryptionWrapper(key_value=redis, fernet=fernet),
        base_url=_base_url(),
        resource_base_url=_base_url(),
        required_scopes=list(SCOPES),
        allowed_client_redirect_uris=ALLOWED_CLIENT_REDIRECT_URIS,
    )
    tokens = _static_tokens()
    if not tokens:
        return github
    # MultiAuth delegates get_routes/get_well_known_routes to `server`, so the
    # OAuth metadata and callback endpoints stay exactly as they are without the
    # static channel -- it only adds a second way to verify a bearer token.
    return MultiAuth(server=github, verifiers=[StaticTokenVerifier(tokens)])


def _authorized_github_user(ctx: AuthContext) -> bool:
    login = ((ctx.token.claims or {}).get("login") if ctx.token else "") or ""
    return login.casefold() in _github_users()


def _upstream_url() -> str:
    base = os.environ.get(UPSTREAM_URL_ENV, "").strip() or DEFAULT_UPSTREAM_URL
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"{UPSTREAM_URL_ENV} must be an HTTPS URL")
    if parsed.query:
        raise RuntimeError(
            f"{UPSTREAM_URL_ENV} must not carry a query string; select tools with {UPSTREAM_TOOLS_ENV}"
        )
    tools = os.environ.get(UPSTREAM_TOOLS_ENV, "").strip() or DEFAULT_UPSTREAM_TOOLS
    if not _TOOLS_PATTERN.fullmatch(tools):
        raise RuntimeError(
            f"{UPSTREAM_TOOLS_ENV} must be a comma-separated list of tool names, got {tools!r}"
        )
    # Interpolated rather than urlencoded so the query reads exactly as Exa
    # documents it (`?tools=a,b`); the pattern above is what makes that safe.
    return f"{base}?tools={tools}"


def _upstream_headers() -> dict[str, str]:
    return {"x-api-key": _required(API_KEY_ENV)}


def _make_upstream_client() -> ProxyClient:
    transport = StreamableHttpTransport(url=_upstream_url(), headers=_upstream_headers())
    client = ProxyClient(transport, timeout=UPSTREAM_TIMEOUT_SECONDS)
    # ProxyClient's constructor turns this on, which would ship the caller's
    # Authorization header -- a JWT this server issued, scoped to this server --
    # to Exa alongside the API key. That hands a third party a credential it has
    # no business holding, and invites it to authenticate against the bearer it
    # cannot validate instead of the key it can. The API key is the only
    # credential that should ever leave this process.
    transport.forward_incoming_headers = False
    return client


AUTH_PROVIDER = _auth_provider()
mcp = FastMCPProxy(
    client_factory=_make_upstream_client,
    name="Exa",
    version="0.1.0",
    instructions=(
        "Passthrough to Exa's official hosted MCP server. Every tool, parameter, and response "
        "is forwarded verbatim from https://mcp.exa.ai/mcp, so Exa's own documentation at "
        "https://exa.ai/docs/reference/exa-mcp applies unchanged. This deployment supplies the "
        "Exa API key server-side; never pass one. A 'server is at capacity' error means nothing "
        "ran -- retry in a few seconds."
    ),
    website_url=_base_url(),
    auth=AUTH_PROVIDER,
    middleware=[AuthMiddleware(auth=_authorized_github_user), ConcurrencyMiddleware()],
)


async def _probe_upstream() -> list[str]:
    """Return the tool names currently mirrored from upstream.

    This checks reachability and the handshake, not the API key: Exa's MCP
    gateway answers initialize and tools/list for any syntactically present
    key and only authenticates on an actual tool call, so a rejected key
    cannot be detected here. Use `scripts/apikey.sh verify`, which probes the
    REST API, to validate the key itself.
    """
    client = Client(
        transport=StreamableHttpTransport(url=_upstream_url(), headers=_upstream_headers()),
        timeout=BOOT_PROBE_TIMEOUT_SECONDS,
    )
    async with client:
        return [tool.name for tool in await client.list_tools()]


def _check_upstream() -> None:
    # The container healthcheck only opens a socket, so without this a server
    # that cannot reach Exa at all looks healthy while serving an empty tool
    # list. Logging the mirrored tools at boot makes that visible immediately.
    try:
        tools = asyncio.run(_probe_upstream())
    except Exception as error:  # noqa: BLE001 - classified below, not swallowed
        text = f"{type(error).__name__}: {error}".casefold()
        if any(marker in text for marker in ("401", "403", "unauthor", "forbidden", "api key")):
            raise RuntimeError(
                f"upstream refused the handshake with the configured {API_KEY_ENV}: {error}. "
                "Validate the key with scripts/apikey.sh verify."
            ) from error
        # A network blip at boot would otherwise crash-loop the container while
        # the key is perfectly fine; the proxy recovers on its own once reachable.
        LOGGER.warning("could not reach upstream Exa MCP at boot: %s", error)
        return
    LOGGER.info("proxying %d upstream Exa tools: %s", len(tools), ", ".join(sorted(tools)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # Fail at boot rather than on every call: a missing key or a malformed
    # static-token list otherwise leaves the container healthy while every tool
    # call fails.
    _required(API_KEY_ENV)
    _upstream_url()
    _static_tokens()
    if os.environ.get(BOOT_PROBE_ENV, "1") != "0":
        _check_upstream()
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
