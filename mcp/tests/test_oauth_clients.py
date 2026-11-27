"""OAuth client compatibility contract shared by every MCP service.

The static checks run anywhere. The registration checks import each service and
need its dependencies, so run them from the mcp/ directory with that service's
environment (running from the repository root clashes with the mcp package):

    MCP_SERVICE=exa-mcp exa-mcp/deploy/python/.venv/bin/python -B -m unittest tests.test_oauth_clients

Without MCP_SERVICE every service is tried and those that cannot be imported are
skipped.
"""
import ast
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVERS = {
    'brave-search-mcp': ('server.py', 'BRAVE_MCP'),
    'firecrawl-mcp': ('server.py', 'FIRECRAWL_MCP'),
    'exa-mcp': ('server.py', 'EXA_MCP'),
    'lark-cli-mcp': ('server.py', 'LARK_CLI_MCP'),
    'colab-cli-mcp': ('server.py', 'COLAB_MCP'),
    'lark-doc': ('scripts/mcp_server.py', 'LARK_MCP'),
}

GEMINI_WEB = [
    f'https://{host}.googleusercontent.com/{path}/user_bound_custom-mcp-*'
    for host in ('oauth-redirect', 'oauth-redirect-sandbox', 'oauth-redirect-test')
    for path in ('r', 'a')
]
# Callbacks each client documents or was observed sending. A client fails to
# register when any URI in its request is missing, so the whole set must stay.
REQUIRED_CALLBACKS = {
    'Claude web, Desktop, mobile, Cowork': ['https://claude.ai/api/mcp/auth_callback',
                                            'https://claude.com/api/mcp/auth_callback'],
    'Claude Desktop plugin DCR': ['claude://claude.ai/mcp-auth-callback/sdk'],
    'Claude Desktop managed OAuth': ['http://127.0.0.1:53280/callback'],
    'Claude Code': ['http://localhost:3118/callback', 'http://127.0.0.1:3118/callback'],
    'ChatGPT': ['https://chatgpt.com/connector_platform_oauth_redirect',
                'https://chatgpt.com/connector/oauth/AbC123'],
    'Codex CLI': ['http://127.0.0.1:45249/callback/SnTi2XH0tVcp',
                  'http://127.0.0.1:45249/callback'],
    'Gemini web': [uri.replace('*', 'example') for uri in GEMINI_WEB],
    'Gemini CLI': ['http://localhost:7777/oauth/callback'],
    'Gemini Enterprise': ['https://vertexaisearch.cloud.google.com/oauth-redirect'],
    'Google Antigravity': ['https://antigravity.google/oauth-callback'],
    'Grok': ['https://grok.com/connectors-oauth-exchange-code/',
             'https://console.x.ai/connectors-oauth-exchange-code/'],
    'Cursor': ['http://localhost:8787/callback', 'https://cursor.com/agents/mcp/oauth/callback',
               'https://www.cursor.com/agents/mcp/oauth/callback',
               'cursor://anysphere.cursor-mcp/oauth/callback'],
    'VS Code': ['http://127.0.0.1:33418/', 'https://vscode.dev/redirect'],
    'MCP Inspector': ['http://localhost:6274/oauth/callback'],
    'IPv6 loopback': ['http://[::1]:5555/callback'],
}
REJECTED_CALLBACKS = [
    'https://oauth-redirect.googleusercontent.com/r/unrelated',
    'https://evil.example.com/callback',
    'https://chatgpt.com/other',
    'http://evil.example.com:8080/callback',
]


def _allowlist(service):
    """Resolve ALLOWED_CLIENT_REDIRECT_URIS from source without importing it."""
    path, _ = SERVERS[service]
    tree = ast.parse((ROOT / service / path).read_text(encoding='utf-8'))
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                constants[name] = value.value
            elif name == 'ALLOWED_CLIENT_REDIRECT_URIS':
                return [item.value if isinstance(item, ast.Constant) else constants[item.id]
                        for item in value.elts]
    raise AssertionError(f'{service} has no ALLOWED_CLIENT_REDIRECT_URIS')


class AllowlistTests(unittest.TestCase):
    def test_services_share_one_allowlist_apart_from_their_workbuddy_callback(self):
        shared = {}
        for service in SERVERS:
            with self.subTest(service=service):
                allowlist = _allowlist(service)
                workbuddy = [uri for uri in allowlist if uri.startswith('workbuddy://')]
                self.assertEqual(len(workbuddy), 1)
                shared[service] = [uri for uri in allowlist if uri not in workbuddy]
        first = next(iter(shared.values()))
        for service, allowlist in shared.items():
            with self.subTest(service=service):
                self.assertEqual(allowlist, first)

    def test_gemini_web_hosts_and_paths_are_all_allowed(self):
        for service in SERVERS:
            with self.subTest(service=service):
                allowlist = _allowlist(service)
                for pattern in GEMINI_WEB:
                    self.assertIn(pattern, allowlist)


def _base_url(service):
    # lark-cli-mcp requires its public origin to start with lark.
    return 'https://lark.example.com' if service == 'lark-cli-mcp' else f'https://{service}.example.com'


def _load(service):
    path, prefix = SERVERS[service]
    environment = {
        **os.environ,
        f'{prefix}_BASE_URL': _base_url(service),
        f'{prefix}_GITHUB_CLIENT_ID': 'client', f'{prefix}_GITHUB_CLIENT_SECRET': 'secret',
        f'{prefix}_GITHUB_USERS': 'Your-GitHub-Login', f'{prefix}_JWT_SIGNING_KEY': 'a' * 64,
        f'{prefix}_STORAGE_KEY': 'hCiJ0nRZ1zHMS0ZbLhCiT6VJmwCJ7bYbTuEIhTVpBAY=',
        f'{prefix}_REDIS_PASSWORD': 'redis-password', 'EXA_MCP_BOOT_PROBE': '0',
    }
    spec = importlib.util.spec_from_file_location(f'oauth_contract_{service.replace("-", "_")}',
                                                  ROOT / service / path)
    module = importlib.util.module_from_spec(spec)
    # Register the module and its directory as a normal import would, so sibling
    # imports and pydantic's annotation lookups resolve.
    sys.modules[spec.name] = module
    with patch.dict(os.environ, environment, clear=True), \
            patch.object(sys, 'path', [str((ROOT / service / path).parent), *sys.path]):
        spec.loader.exec_module(module)
    return module


def _services_under_test():
    selected = os.environ.get('MCP_SERVICE')
    return [selected] if selected else list(SERVERS)


class RegistrationTests(unittest.TestCase):
    def test_registration_metadata_and_token_auth(self):
        for service in _services_under_test():
            with self.subTest(service=service):
                try:
                    module = _load(service)
                    try:
                        import httpx
                    except ImportError:
                        import httpx2 as httpx
                    from key_value.aio.stores.memory import MemoryStore
                    from starlette.applications import Starlette
                except ImportError as error:
                    if os.environ.get('MCP_SERVICE'):
                        raise
                    self.skipTest(f'{service} dependencies are not installed: {error}')
                asyncio.run(self._exercise(service, module, httpx, MemoryStore, Starlette))

    async def _exercise(self, service, module, httpx, MemoryStore, Starlette):
        base_url = _base_url(service)
        provider = module._OriginCompatibleGitHubProvider(
            client_id='client', client_secret='secret', jwt_signing_key='a' * 64,
            base_url=base_url, resource_base_url=base_url, required_scopes=['read:user'],
            client_storage=MemoryStore(),
            allowed_client_redirect_uris=module.ALLOWED_CLIENT_REDIRECT_URIS,
        )
        app = Starlette(routes=provider.get_routes('/mcp'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as client:
            metadata = (await client.get('/.well-known/oauth-authorization-server')).json()
            methods = metadata['token_endpoint_auth_methods_supported']
            self.assertEqual(methods[:2], ['client_secret_post', 'client_secret_basic'])
            self.assertIn('none', methods)
            self.assertTrue(metadata['client_id_metadata_document_supported'])

            async def register(redirect_uris, method='client_secret_post'):
                return await client.post('/register', json={
                    'client_name': 'contract', 'redirect_uris': redirect_uris,
                    'grant_types': ['authorization_code', 'refresh_token'],
                    'response_types': ['code'], 'token_endpoint_auth_method': method,
                })

            for name, uris in REQUIRED_CALLBACKS.items():
                response = await register(uris)
                self.assertEqual(response.status_code, 201, f'{name}: {response.text}')
            for uri in REJECTED_CALLBACKS:
                self.assertEqual((await register([uri])).status_code, 400, uri)

            for method in ('client_secret_post', 'client_secret_basic'):
                registered = (await register(REQUIRED_CALLBACKS['Gemini web'], method)).json()
                self.assertEqual(registered['token_endpoint_auth_method'], method)
                self.assertTrue(registered['client_secret'])
                stored = await provider.get_client(registered['client_id'])
                self.assertEqual(stored.client_secret, registered['client_secret'])
            public = (await register(['http://localhost:7777/oauth/callback'], 'none')).json()
            self.assertEqual(public['token_endpoint_auth_method'], 'none')
            self.assertNotIn('client_secret', public)

            confidential = (await register(REQUIRED_CALLBACKS['Gemini web'])).json()
            token = await client.post('/token', data={
                'grant_type': 'authorization_code', 'code': 'unknown',
                'redirect_uri': REQUIRED_CALLBACKS['Gemini web'][0], 'code_verifier': 'x' * 43,
                'client_id': confidential['client_id'], 'client_secret': 'wrong',
            })
            self.assertEqual(token.status_code, 401)
            self.assertEqual(token.json()['error'], 'invalid_client')


if __name__ == '__main__':
    unittest.main()
