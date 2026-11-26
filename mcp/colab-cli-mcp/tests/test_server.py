"""Verify the public MCP route and translate against the installed official CLI."""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from colab_cli.cli import app as upstream_app
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.stores.memory import MemoryStore
import httpx2
from typer.main import get_command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
import server


class CommandTests(unittest.TestCase):
    def test_agent_guide_examples_pass_argument_validation(self):
        guide = Path(__file__).resolve().parents[1] / "AGENTS.md"
        examples = [
            json.loads(block)
            for block in re.findall(r"```json\n(.*?)\n```", guide.read_text(), flags=re.S)
        ]
        self.assertGreaterEqual(len(examples), 8)
        for example in examples:
            with self.subTest(args=example["args"]):
                cli.parse_command(
                    example["args"], example.get("stdin"), example.get("file_base64"),
                )

    def test_translated_arguments_parse_with_official_cli(self):
        commands = [
            (["new", "-s", "demo", "--gpu=T4", "--high-mem"], {}),
            (["new", "--tpu", "v5e1"], {}),
            (["sessions"], {}), (["status", "-s", "demo"], {}),
            (["restart-kernel", "-s", "demo"], {}), (["stop", "-s", "demo"], {}),
            (["url", "-s", "demo"], {}), (["usage"], {}), (["version"], {}),
            (["exec", "-s", "demo", "--timeout=0"], {"stdin": "print(4)"}),
            (["exec", "-f", "demo.ipynb"], {"file_base64": "e30="}),
            (["run", "--keep", "main.py", "--", "--title", "two words"], {"file_base64": "eA=="}),
            (["install", "-s", "demo", "numpy[extra]>=2"], {}),
            (["ls", "/content"], {}), (["rm", "/content/file"], {}),
            (["upload", "demo", "/content/demo"], {"file_base64": ""}),
            (["download", "/content/a b", "demo"], {}),
            (["log", "-s", "demo", "-n", "1", "-t", "execution", "-o", "log.ipynb"], {}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            upstream = get_command(upstream_app)
            for args, kwargs in commands:
                with self.subTest(args=args):
                    parsed = cli.parse_command(args, kwargs.get("stdin"), kwargs.get("file_base64"))
                    argv, output = cli._prepare(parsed, work)
                    with upstream.make_context("colab", ["--auth", "adc", *argv]) as ctx:
                        subcommand = upstream.get_command(ctx, argv[0])
                        with subcommand.make_context(argv[0], argv[1:], parent=ctx) as call:
                            if argv[0] == "run":
                                self.assertEqual(call.params["script"], "./main.py")
                                self.assertEqual(call.params["script_args"], ["--title", "two words"])
                            if argv[0] == "download":
                                self.assertEqual(call.params["remote_path"], "/content/a b")
                                self.assertEqual(call.params["local_path"], f"./{output}")

    def test_invalid_inputs_are_rejected_before_cli_execution(self):
        cases = [
            (["--auth", "oauth2", "usage"], {}),
            (["exec", "--config=/tmp/session"], {"stdin": "print(4)"}),
            (["exec", "--timeout", "nan"], {"stdin": "print(4)"}),
            (["exec", "-s", "../state"], {"stdin": "print(4)"}),
            (["exec", "-f", "/etc/passwd"], {"file_base64": "eA=="}),
            (["exec", "--output-image=/tmp/file"], {"stdin": "print(4)"}),
            (["exec"], {"stdin": "x" * (cli.MAX_INPUT + 1)}),
            (["new", "--gpu=T4", "--tpu=v6e1"], {}),
            (["stop", "-s", "old", "--session=new"], {}),
            (["install", "x'bad"], {}),
            (["install", "-r", "server.txt"], {}),
            (["log", "-s", "demo", "-o", "../adc.json"], {}),
            (["log", "-s", "demo", "-n", "-1"], {}),
            (["upload", "x", "/content/../x"], {"file_base64": ""}),
            (["upload", "x", "y"], {"file_base64": "not base64"}),
            (["upload", "x", "y"], {"file_base64": "AAAA" * ((cli.MAX_FILE // 3) + 1)}),
            (["upload", "x", "y"], {}),
            (["download", "x", "/tmp/out"], {}),
            (["version"], {"stdin": "unexpected"}),
            (["url", "--open"], {}), (["drivemount"], {}), (["ssh"], {}),
            (["update", "--install"], {}),
        ]
        with patch.object(cli, "_run") as runner:
            for args, kwargs in cases:
                with self.subTest(args=args):
                    with self.assertRaises(ValueError):
                        cli.call_cli(args, **kwargs)
            runner.assert_not_called()

    def test_file_round_trip_and_cleanup(self):
        content = b"\x00\xfftest"
        encoded = base64.b64encode(content).decode()
        work_dirs = []

        def fake_run(argv, stdin, work):
            work_dirs.append(work)
            if argv[0] == "upload":
                self.assertEqual((work / "input.bin").read_bytes(), content)
                self.assertEqual(argv[-1], "/content/input.bin")
            else:
                (work / "output.bin").write_bytes(content)
            return {"exit_code": 0}

        with patch.object(cli, "_run", side_effect=fake_run):
            cli.call_cli(["upload", "input.bin", "/content/input.bin"], file_base64=encoded)
            result = cli.call_cli(["download", "/content/input.bin", "output.bin"])
        self.assertEqual(result["file"], {"name": "output.bin", "base64": encoded})
        self.assertTrue(all(not work.exists() for work in work_dirs))

    def test_file_export_errors_are_explicit(self):
        def fake_run(argv, stdin, work):
            if argv[-1] == "./oversize":
                with (work / "oversize").open("wb") as file:
                    file.truncate(cli.MAX_FILE + 1)
            return {"exit_code": 0}

        with patch.object(cli, "_run", side_effect=fake_run):
            for name in ("missing", "oversize"):
                result = cli.call_cli(["download", "remote", name])
                self.assertIn("file_error", result)
                self.assertNotIn("file", result)

    def test_busy_call_never_runs(self):
        with cli._CALL_SLOT, patch.object(cli, "_run") as runner:
            with self.assertRaisesRegex(RuntimeError, "did not start"):
                cli.call_cli(["sessions"])
            runner.assert_not_called()

    def test_duplicate_session_name_never_replaces_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / ".config/colab-cli"
            config.mkdir(parents=True)
            (config / "sessions.json").write_text('{"demo": {}}')
            with (
                patch.dict(os.environ, {"COLAB_MCP_STATE_DIR": temporary}),
                patch.object(cli, "_run") as runner,
                self.assertRaisesRegex(ValueError, "session name already exists"),
            ):
                cli.call_cli(["new", "-s", "demo"])
            runner.assert_not_called()

    def test_bounded_streams_stdin_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            entry = work / "colab_entry.py"
            entry.write_text(
                "import os, sys\n"
                "assert 'COLAB_MCP_GITHUB_CLIENT_SECRET' not in os.environ\n"
                "print(sys.stdin.read())\n"
                "print('x' * 1000)\n"
                "print('url?colab-runtime-proxy-token=your-token&other=1', file=sys.stderr)\n"
            )
            with patch.object(cli, "__file__", str(work / "cli.py")), patch.object(cli, "MAX_OUTPUT", 100):
                result = cli._run([], b"test input", work)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(len(result["stdout"]), 100)
                self.assertTrue(result["stdout"].startswith("test input\n"))
                self.assertTrue(result["truncated"])
                self.assertGreater(result["original_bytes"]["stdout"], 100)
                self.assertIn("colab-runtime-proxy-token=[redacted]&other=1", result["stderr"])
            entry.write_text("import time\nprint('started', flush=True)\ntime.sleep(20)\n")
            with patch.object(cli, "__file__", str(work / "cli.py")), patch.object(cli, "TIMEOUT", 0.5):
                result = cli._run([], None, work)
                self.assertTrue(result["timed_out"])
                self.assertIsNone(result["exit_code"])
                self.assertEqual(result["stdout"], "started\n")

    def test_real_cli_version_help_and_history_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / ".config/colab-cli"
            history = config / "history"
            history.mkdir(parents=True)
            (config / "settings.json").write_text('{"enable_update_check": false}')
            (history / "demo.jsonl").write_text(
                '{"event_type":"execution","timestamp":"2026-01-01T00:00:00",'
                '"code":"print(2 + 2)","outputs":[]}\n'
            )
            with patch.dict(os.environ, {"COLAB_MCP_STATE_DIR": temporary}):
                self.assertRegex(cli.call_cli(["version"])["stdout"], r"Version: \d+\.\d+")
                self.assertEqual(cli.call_cli(["run", "--help"])["exit_code"], 0)
                self.assertEqual(cli.call_cli(["upload", "--help"])["exit_code"], 0)
                result = cli.call_cli(["log", "-s", "demo", "-o", "history.jsonl"])
                self.assertEqual(result["exit_code"], 0, result)
                decoded = base64.b64decode(result["file"]["base64"]).decode()
                self.assertIn("print(2 + 2)", decoded)


def _environment():
    return {
        "COLAB_MCP_BASE_URL": "https://colab.example.com",
        "COLAB_MCP_GITHUB_CLIENT_ID": "your-client-id",
        "COLAB_MCP_GITHUB_CLIENT_SECRET": "your-client-secret",
        "COLAB_MCP_GITHUB_USERS": "Your-GitHub-Login",
        "COLAB_MCP_JWT_SIGNING_KEY": "your-test-signing-key",
        "COLAB_MCP_STORAGE_KEY": Fernet.generate_key().decode(),
        "COLAB_MCP_REDIS_PASSWORD": "your-redis-password",
    }


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_access_control_through_http(self):
        with patch.dict(os.environ, _environment(), clear=True):
            mcp = server.create_server()
        app = mcp.http_app(path="/mcp", stateless_http=True)
        identities = {
            "allowed": AccessToken(token="allowed", client_id="test", scopes=["read:user"],
                                   claims={"login": "your-github-login"}),
            "denied": AccessToken(token="denied", client_id="test", scopes=["read:user"],
                                  claims={"login": "another-login"}),
        }
        async with app.lifespan(app), httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://colab.example.com"
        ) as client:
            metadata = await client.get("/.well-known/oauth-authorization-server")
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["issuer"], "https://colab.example.com/")
            protected = await client.get("/.well-known/oauth-protected-resource/mcp")
            self.assertEqual(protected.status_code, 200)
            self.assertEqual(protected.json()["resource"], "https://colab.example.com/mcp")
            with (
                patch.object(mcp.auth, "verify_token", AsyncMock(side_effect=identities.get)),
                patch.object(server, "call_cli", return_value={"exit_code": 0}) as runner,
            ):
                async def request(method, identity=None, params=None):
                    headers = {
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-11-25",
                    }
                    if identity:
                        headers["Authorization"] = f"Bearer {identity}"
                    return await asyncio.wait_for(client.post("/mcp", headers=headers, json={
                        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
                    }), 5)

                def payload(response):
                    if response.headers["content-type"].startswith("application/json"):
                        return response.json()
                    events = (json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: "))
                    return next(message for message in events if "result" in message or "error" in message)

                unauth = await request("tools/list")
                self.assertEqual(unauth.status_code, 401)
                initialize = await request("initialize", "allowed", {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.0"},
                })
                self.assertEqual(payload(initialize)["result"]["serverInfo"]["name"], "Colab-CLI")
                denied = await request("tools/list", "denied")
                self.assertEqual(payload(denied)["result"]["tools"], [])
                params = {"name": "colab_cli", "arguments": {"args": ["version"]}}
                denied_call = payload(await request("tools/call", "denied", params))
                self.assertTrue("error" in denied_call or denied_call["result"]["isError"])
                runner.assert_not_called()
                allowed = await request("tools/list", "allowed")
                tools = payload(allowed)["result"]["tools"]
                self.assertEqual([tool["name"] for tool in tools], ["colab_cli"])
                tool = tools[0]
                advertised_commands = set(re.findall(r"^- `([a-z-]+)(?:`| )", tool["description"], re.M))
                self.assertEqual(advertised_commands, set(cli._OPTIONS))
                schema = tool["inputSchema"]
                self.assertEqual(schema["required"], ["args"])
                fields = schema["properties"]
                self.assertEqual(set(fields), {"args", "stdin", "file_base64"})
                self.assertEqual(fields["args"]["minItems"], 1)
                self.assertEqual(fields["args"]["maxItems"], 128)
                for name, text in [
                    ("args", "4096 UTF-8 bytes"), ("stdin", "Required for exec"),
                    ("file_base64", "Required for upload, run and exec"),
                ]:
                    self.assertIn(text, fields[name]["description"])
                    self.assertTrue(fields[name]["examples"])
                result = payload(await request("tools/call", "allowed", params))["result"]
                self.assertEqual(result["structuredContent"], {"exit_code": 0})
                runner.assert_called_once_with(["version"], None, None)

    async def test_confidential_dcr_clients_keep_their_secret(self):
        with patch.dict(os.environ, _environment(), clear=True):
            mcp = server.create_server()
        mcp.auth._client_store = PydanticAdapter[ProxyDCRClient](
            key_value=MemoryStore(), pydantic_model=ProxyDCRClient,
            default_collection="mcp-oauth-proxy-clients", raise_on_validation_error=True,
        )
        app = mcp.http_app(path="/mcp", stateless_http=True)
        redirect = "https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-abc123"
        async with app.lifespan(app), httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://colab.example.com"
        ) as client:
            metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
            self.assertEqual(metadata["issuer"], "https://colab.example.com/")
            self.assertEqual(metadata["token_endpoint_auth_methods_supported"],
                             ["client_secret_post", "client_secret_basic", "none", "private_key_jwt"])

            async def register(method):
                response = await client.post("/register", json={
                    "client_name": "gemini", "redirect_uris": [redirect],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"], "token_endpoint_auth_method": method,
                })
                self.assertEqual(response.status_code, 201, response.text)
                return response.json()

            for method in server.CONFIDENTIAL_AUTH_METHODS:
                registered = await register(method)
                self.assertEqual(registered["token_endpoint_auth_method"], method)
                self.assertTrue(registered["client_secret"])
                stored = await mcp.auth.get_client(registered["client_id"])
                self.assertEqual(stored.token_endpoint_auth_method, method)
                self.assertEqual(stored.client_secret, registered["client_secret"])

            gemini = await client.post("/register", json={
                "client_name": "Google",
                "redirect_uris": [
                    f"https://{host}.googleusercontent.com/{path}/user_bound_custom-mcp-abc123"
                    for host in ("oauth-redirect-sandbox", "oauth-redirect-test", "oauth-redirect")
                    for path in ("r", "a")
                ],
                "response_types": ["code"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "client_secret_post",
            })
            self.assertEqual(gemini.status_code, 201, gemini.text)
            self.assertTrue(gemini.json()["client_secret"])
            rejected = await client.post("/register", json={
                "redirect_uris": ["https://oauth-redirect.googleusercontent.com/r/unrelated"],
            })
            self.assertEqual(rejected.status_code, 400)

            public = await register("none")
            self.assertEqual(public["token_endpoint_auth_method"], "none")
            self.assertNotIn("client_secret", public)

            confidential = await register("client_secret_post")
            token = await client.post("/token", data={
                "grant_type": "authorization_code", "code": "unknown",
                "redirect_uri": redirect, "code_verifier": "x" * 43,
                "client_id": confidential["client_id"], "client_secret": "wrong",
            })
            self.assertEqual(token.status_code, 401)
            self.assertEqual(token.json()["error"], "invalid_client")

    async def test_rejects_incomplete_auth_config(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_USERS"):
                server.create_server()


if __name__ == "__main__":
    unittest.main()
