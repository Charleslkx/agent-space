"""Tests for the Exa MCP passthrough proxy.

The server is loaded via importlib with a patched environment so that nothing
here needs a real Redis, a real GitHub OAuth app, or a real Exa API key. The
proxy-mirroring tests run against an in-memory FastMCP server standing in for
mcp.exa.ai, so the suite never touches the network.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"

BASE_ENV = {
    "EXA_MCP_BASE_URL": "https://exa.example.com",
    "EXA_MCP_GITHUB_CLIENT_ID": "client",
    "EXA_MCP_GITHUB_CLIENT_SECRET": "secret",
    "EXA_MCP_GITHUB_USERS": "Approved-User,second-user",
    "EXA_MCP_JWT_SIGNING_KEY": "a" * 64,
    # A syntactically valid Fernet key; never used against a real store here.
    "EXA_MCP_STORAGE_KEY": "hCiJ0nRZ1zHMS0ZbLhCiT6VJmwCJ7bYbTuEIhTVpBAY=",
    "EXA_MCP_REDIS_PASSWORD": "redis-password",
    "EXA_API_KEY": "exa-api-key",
    "EXA_MCP_BOOT_PROBE": "0",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}

VALID_TOKEN = "t" * 64
OTHER_TOKEN = "u" * 64


def load_server(**overrides: str):
    """Load a fresh copy of server.py under a patched environment."""
    environment = {**BASE_ENV, **overrides}
    # Drop keys explicitly overridden to None so "unset" is testable.
    environment = {k: v for k, v in environment.items() if v is not None}
    spec = importlib.util.spec_from_file_location("exa_mcp_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


MODULE = load_server()


class UpstreamUrlTests(unittest.TestCase):
    def test_defaults_to_exa_hosted_endpoint_with_all_tools(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            url = MODULE._upstream_url()
        self.assertEqual(
            url,
            "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa,agent_run,"
            "web_search_advanced_exa",
        )

    def test_commas_are_not_percent_encoded(self):
        # Exa documents the parameter as `?tools=a,b`; keep it literally that.
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertNotIn("%2C", MODULE._upstream_url())

    def test_tool_selection_is_overridable(self):
        with patch.dict(
            os.environ, {**BASE_ENV, "EXA_MCP_UPSTREAM_TOOLS": "web_search_exa"}, clear=True
        ):
            self.assertEqual(
                MODULE._upstream_url(), "https://mcp.exa.ai/mcp?tools=web_search_exa"
            )

    def test_rejects_tool_list_that_could_smuggle_query_parameters(self):
        for value in ("web_search_exa&foo=bar", "a b", "a,,b", "a/b", ""):
            with self.subTest(value=value):
                env = {**BASE_ENV, "EXA_MCP_UPSTREAM_TOOLS": value}
                with patch.dict(os.environ, env, clear=True):
                    if value == "":
                        # Empty falls back to the default rather than erroring.
                        self.assertIn("web_search_exa", MODULE._upstream_url())
                    else:
                        with self.assertRaises(RuntimeError):
                            MODULE._upstream_url()

    def test_rejects_upstream_url_with_query_string(self):
        env = {**BASE_ENV, "EXA_MCP_UPSTREAM_URL": "https://mcp.exa.ai/mcp?tools=x"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                MODULE._upstream_url()

    def test_rejects_plaintext_upstream_url(self):
        env = {**BASE_ENV, "EXA_MCP_UPSTREAM_URL": "http://mcp.exa.ai/mcp"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                MODULE._upstream_url()


class UpstreamCredentialTests(unittest.TestCase):
    def test_api_key_is_sent_as_x_api_key_header(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertEqual(MODULE._upstream_headers(), {"x-api-key": "exa-api-key"})

    def test_missing_api_key_is_an_error(self):
        env = {k: v for k, v in BASE_ENV.items() if k != "EXA_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                MODULE._upstream_headers()

    def test_caller_credentials_are_never_forwarded_upstream(self):
        # ProxyClient's constructor turns header forwarding on. Left on, the
        # caller's Authorization header -- a JWT this server minted, which Exa
        # cannot validate -- would be sent alongside the API key and Exa would
        # reject the request. This is the single most load-bearing line in the
        # proxy setup, so assert it directly.
        with patch.dict(os.environ, BASE_ENV, clear=True):
            client = MODULE._make_upstream_client()
        self.assertFalse(client.transport.forward_incoming_headers)
        self.assertEqual(client.transport.headers, {"x-api-key": "exa-api-key"})


class BaseUrlTests(unittest.TestCase):
    def test_accepts_https_origin(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertEqual(MODULE._base_url(), "https://exa.example.com")

    def test_rejects_non_https_or_pathful_origins(self):
        for value in ("http://exa.example.com", "https://exa.example.com/mcp", "exa.example.com"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {**BASE_ENV, "EXA_MCP_BASE_URL": value}, clear=True):
                    with self.assertRaises(RuntimeError):
                        MODULE._base_url()


class RedirectUriTests(unittest.TestCase):
    def test_every_supported_client_has_a_callback(self):
        allowed = MODULE.ALLOWED_CLIENT_REDIRECT_URIS
        expected = [
            "https://chatgpt.com/connector/oauth/*",
            "https://chatgpt.com/connector_platform_oauth_redirect",
            "https://claude.ai/api/mcp/auth_callback",
            "https://grok.com/connectors-oauth-exchange-code/",
            "https://www.cursor.com/agents/mcp/oauth/callback",
            "workbuddy://workbuddy/mcp/custom-mcp%3Aexa/oauth/callback",
            # Claude Code, Codex, VS Code, Zed, Gemini CLI all use loopback.
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
        for uri in expected:
            with self.subTest(uri=uri):
                self.assertIn(uri, allowed)

    def test_workbuddy_callback_is_namespaced_to_this_service(self):
        # A shared namespace across sibling services would let one service's
        # registration satisfy another's callback.
        self.assertIn("custom-mcp%3Aexa", MODULE.WORKBUDDY_REDIRECT_URI)


class StaticTokenParsingTests(unittest.TestCase):
    def test_unset_means_oauth_only(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertEqual(MODULE._static_tokens(), {})

    def test_maps_token_to_lowercased_login(self):
        env = {**BASE_ENV, "EXA_MCP_STATIC_TOKENS": f"Approved-User:{VALID_TOKEN}"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(MODULE._static_tokens(), {VALID_TOKEN: "approved-user"})

    def test_parses_multiple_entries(self):
        env = {
            **BASE_ENV,
            "EXA_MCP_STATIC_TOKENS": f"approved-user:{VALID_TOKEN}, second-user:{OTHER_TOKEN}",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                MODULE._static_tokens(),
                {VALID_TOKEN: "approved-user", OTHER_TOKEN: "second-user"},
            )

    def test_rejects_login_outside_the_allowlist(self):
        # Such a token authenticates but has every tool filtered away, which
        # looks like a broken server rather than a misconfiguration.
        env = {**BASE_ENV, "EXA_MCP_STATIC_TOKENS": f"stranger:{VALID_TOKEN}"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                MODULE._static_tokens()
        self.assertIn("EXA_MCP_GITHUB_USERS", str(ctx.exception))

    def test_rejects_short_token(self):
        env = {**BASE_ENV, "EXA_MCP_STATIC_TOKENS": "approved-user:tooshort"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                MODULE._static_tokens()
        self.assertIn("32", str(ctx.exception))

    def test_rejects_malformed_entry(self):
        for value in (f"{VALID_TOKEN}", f"approved-user:", f":{VALID_TOKEN}"):
            with self.subTest(value=value):
                env = {**BASE_ENV, "EXA_MCP_STATIC_TOKENS": value}
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(RuntimeError):
                        MODULE._static_tokens()

    def test_rejects_same_token_for_two_logins(self):
        env = {
            **BASE_ENV,
            "EXA_MCP_STATIC_TOKENS": f"approved-user:{VALID_TOKEN},second-user:{VALID_TOKEN}",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                MODULE._static_tokens()


class AuthProviderTests(unittest.TestCase):
    def test_oauth_only_when_no_static_tokens_configured(self):
        module = load_server()
        self.assertIsInstance(module.AUTH_PROVIDER, MODULE.GitHubProvider)

    def test_static_channel_is_added_alongside_oauth(self):
        module = load_server(EXA_MCP_STATIC_TOKENS=f"approved-user:{VALID_TOKEN}")
        self.assertIsInstance(module.AUTH_PROVIDER, MODULE.MultiAuth)
        # OAuth must remain the route owner: MultiAuth delegates get_routes to
        # `server`, so the metadata and callback endpoints are unchanged.
        self.assertIsInstance(module.AUTH_PROVIDER.server, module.GitHubProvider)
        self.assertEqual(
            [type(v).__name__ for v in module.AUTH_PROVIDER.verifiers],
            ["StaticTokenVerifier"],
        )

    def test_static_token_verifies_and_carries_its_login(self):
        module = load_server(EXA_MCP_STATIC_TOKENS=f"approved-user:{VALID_TOKEN}")
        token = asyncio.run(module.AUTH_PROVIDER.verify_token(VALID_TOKEN))
        self.assertIsNotNone(token)
        self.assertEqual(token.claims["login"], "approved-user")
        self.assertEqual(token.claims["auth_channel"], "static_token")
        self.assertEqual(token.scopes, ["read:user"])

    def test_wrong_static_token_is_rejected(self):
        module = load_server(EXA_MCP_STATIC_TOKENS=f"approved-user:{VALID_TOKEN}")
        self.assertIsNone(asyncio.run(module.AUTH_PROVIDER.verify_token(OTHER_TOKEN)))

    def test_prefix_of_a_valid_token_is_rejected(self):
        module = load_server(EXA_MCP_STATIC_TOKENS=f"approved-user:{VALID_TOKEN}")
        self.assertIsNone(asyncio.run(module.AUTH_PROVIDER.verify_token(VALID_TOKEN[:-1])))


class AuthorizationTests(unittest.TestCase):
    class _Token:
        def __init__(self, claims):
            self.claims = claims

    class _Ctx:
        def __init__(self, token):
            self.token = token

    def _allows(self, module, claims):
        return module._authorized_github_user(self._Ctx(self._Token(claims)))

    def test_allowlist_is_case_insensitive(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertTrue(self._allows(MODULE, {"login": "approved-user"}))
            self.assertTrue(self._allows(MODULE, {"login": "APPROVED-USER"}))

    def test_unknown_login_is_denied(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertFalse(self._allows(MODULE, {"login": "stranger"}))

    def test_missing_token_is_denied(self):
        with patch.dict(os.environ, BASE_ENV, clear=True):
            self.assertFalse(MODULE._authorized_github_user(self._Ctx(None)))

    def test_static_token_passes_the_same_allowlist_check(self):
        # Both channels must land on one authorization rule, otherwise the
        # static channel silently becomes a way around the allowlist.
        module = load_server(EXA_MCP_STATIC_TOKENS=f"approved-user:{VALID_TOKEN}")
        env = {**BASE_ENV, "EXA_MCP_STATIC_TOKENS": f"approved-user:{VALID_TOKEN}"}
        with patch.dict(os.environ, env, clear=True):
            token = asyncio.run(module.AUTH_PROVIDER.verify_token(VALID_TOKEN))
            self.assertTrue(module._authorized_github_user(self._Ctx(token)))


class ConcurrencyMiddlewareTests(unittest.TestCase):
    def test_queues_then_refuses_when_at_capacity(self):
        async def scenario():
            middleware = MODULE.ConcurrencyMiddleware(limit=1)
            started = asyncio.Event()
            release = asyncio.Event()

            async def slow_call(_context):
                started.set()
                await release.wait()
                return "first"

            async def quick_call(_context):
                return "second"

            first = asyncio.ensure_future(middleware.on_call_tool(None, slow_call))
            await started.wait()
            with patch.object(MODULE, "ACQUIRE_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(RuntimeError) as ctx:
                    await middleware.on_call_tool(None, quick_call)
            self.assertIn("at capacity", str(ctx.exception))
            release.set()
            self.assertEqual(await first, "first")
            # The slot must come back once the first call finishes.
            self.assertEqual(await middleware.on_call_tool(None, quick_call), "second")

        asyncio.run(scenario())

    def test_slot_is_released_when_the_call_raises(self):
        async def scenario():
            middleware = MODULE.ConcurrencyMiddleware(limit=1)

            async def boom(_context):
                raise ValueError("upstream blew up")

            async def ok(_context):
                return "ok"

            with self.assertRaises(ValueError):
                await middleware.on_call_tool(None, boom)
            self.assertEqual(await middleware.on_call_tool(None, ok), "ok")

        asyncio.run(scenario())


class ProxyMirroringTests(unittest.TestCase):
    """The proxy must reproduce whatever upstream advertises, verbatim."""

    @staticmethod
    def _fake_upstream():
        from fastmcp import FastMCP

        upstream = FastMCP(name="FakeExa")

        @upstream.tool
        def web_search_exa(query: str, numResults: int = 5) -> str:
            """Search the web for any topic and get clean, ready-to-use content."""
            return f"results for {query} ({numResults})"

        @upstream.tool
        def web_fetch_exa(url: str) -> str:
            """Read a webpage's full content as clean markdown."""
            return f"content of {url}"

        return upstream

    def _proxy_to(self, upstream):
        from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

        return FastMCPProxy(client_factory=lambda: ProxyClient(upstream), name="Exa")

    def test_tool_names_and_schemas_come_from_upstream(self):
        async def scenario():
            from fastmcp import Client

            upstream = self._fake_upstream()
            async with Client(upstream) as direct:
                expected = {t.name: t for t in await direct.list_tools()}
            async with Client(self._proxy_to(upstream)) as proxied:
                actual = {t.name: t for t in await proxied.list_tools()}
            self.assertEqual(sorted(actual), ["web_fetch_exa", "web_search_exa"])
            for name, tool in expected.items():
                self.assertEqual(actual[name].description, tool.description)
                self.assertEqual(actual[name].inputSchema, tool.inputSchema)

        asyncio.run(scenario())

    def test_tool_calls_and_results_pass_through(self):
        async def scenario():
            from fastmcp import Client

            async with Client(self._proxy_to(self._fake_upstream())) as client:
                result = await client.call_tool("web_search_exa", {"query": "exa mcp"})
            self.assertEqual(result.content[0].text, "results for exa mcp (5)")

        asyncio.run(scenario())

    def test_new_upstream_tools_appear_without_code_changes(self):
        # This is the property that makes the passthrough claim hold: Exa can
        # add or change tools and this server needs no edit.
        async def scenario():
            from fastmcp import Client

            upstream = self._fake_upstream()

            @upstream.tool
            def brand_new_exa_tool(prompt: str) -> str:
                """A tool that did not exist when this proxy was written."""
                return prompt

            async with Client(self._proxy_to(upstream)) as client:
                names = {t.name for t in await client.list_tools()}
            self.assertIn("brand_new_exa_tool", names)

        asyncio.run(scenario())


class BootProbeTests(unittest.TestCase):
    def test_refused_handshake_fails_the_boot(self):
        # Note this does NOT amount to key validation: Exa's MCP gateway
        # answers the handshake for any syntactically present key. It only
        # catches an outright refusal. `scripts/apikey.sh verify` is what
        # actually validates the key, against the REST API.
        async def unauthorized():
            raise RuntimeError("Client error '401 Unauthorized' for url 'https://mcp.exa.ai/mcp'")

        with patch.dict(os.environ, BASE_ENV, clear=True):
            with patch.object(MODULE, "_probe_upstream", unauthorized):
                with self.assertRaises(RuntimeError) as ctx:
                    MODULE._check_upstream()
        self.assertIn("upstream refused the handshake", str(ctx.exception))

    def test_transient_network_failure_only_warns(self):
        # Crash-looping the container over a network blip would take the
        # service down for a reason that resolves itself.
        async def unreachable():
            raise ConnectionError("[Errno 111] Connection refused")

        with patch.dict(os.environ, BASE_ENV, clear=True):
            with patch.object(MODULE, "_probe_upstream", unreachable):
                with self.assertLogs(MODULE.LOGGER, level="WARNING"):
                    MODULE._check_upstream()

    def test_successful_probe_logs_the_mirrored_tools(self):
        async def ok():
            return ["web_search_exa", "web_fetch_exa"]

        with patch.dict(os.environ, BASE_ENV, clear=True):
            with patch.object(MODULE, "_probe_upstream", ok):
                with self.assertLogs(MODULE.LOGGER, level="INFO") as logs:
                    MODULE._check_upstream()
        self.assertIn("web_search_exa", "".join(logs.output))


class ServerWiringTests(unittest.TestCase):
    def test_server_is_a_proxy_not_a_local_tool_host(self):
        from fastmcp.server.providers.proxy import FastMCPProxy

        self.assertIsInstance(MODULE.mcp, FastMCPProxy)

    def test_authorization_and_concurrency_middleware_are_installed(self):
        installed = {type(m).__name__ for m in MODULE.mcp.middleware}
        self.assertIn("AuthMiddleware", installed)
        self.assertIn("ConcurrencyMiddleware", installed)

    def test_upstream_read_timeout_covers_a_long_agent_run(self):
        # agent_run's own ceiling upstream is ~750s; a shorter read timeout here
        # would kill long runs before they return a resumable run id.
        self.assertGreater(MODULE.UPSTREAM_TIMEOUT_SECONDS, 750)


if __name__ == "__main__":
    unittest.main()
