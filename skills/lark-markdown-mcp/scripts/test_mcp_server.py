#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp import Client

MODULE_PATH = Path(__file__).with_name("mcp_server.py")
SPEC = importlib.util.spec_from_file_location("lark_markdown_mcp", MODULE_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SERVER)


class MCPServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_registered(self) -> None:
        async with Client(SERVER.mcp) as client:
            tools = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "check_lark_cli", "batch_pull", "batch_push", "point_update",
                "create_document", "insert_media", "whiteboard_query", "whiteboard_update",
            },
        )
        self.assertTrue(all(tool.title for tool in tools))
        self.assertTrue(all(tool.outputSchema for tool in tools))
        self.assertTrue(all(
            tool.meta["securitySchemes"] == SERVER._security_schemes(SERVER.AUTH_MODE)
            for tool in tools
        ))

    async def test_batch_push_cleans_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli:
            async with Client(SERVER.mcp) as client:
                await client.call_tool("batch_push", {"documents": [{
                    "doc": "doc-token", "content": "# title", "mode": "overwrite"
                }]})
            self.assertEqual(run_cli.call_count, 1)
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_lark_cli_success_shape(self) -> None:
        auth = {
            "identity": "user",
            "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        completed = type("Completed", (), {"stdout": "lark-cli version 1.0.56"})()
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", return_value=auth):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertEqual(status["user_status"], "ready")
        self.assertTrue(status["verified"])

    def test_lark_cli_retries_transient_auth_failure(self) -> None:
        unavailable = {"verified": False, "identities": {"user": {}}}
        ready = {
            "identity": "user",
            "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        completed = type("Completed", (), {"stdout": "lark-cli version 1.0.56"})()
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", side_effect=[unavailable, ready]), \
             patch.object(SERVER.time, "sleep"):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertTrue(status["verified"])

    def test_hidden_payload_is_removed_after_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"):
            with self.assertRaises(RuntimeError):
                with SERVER._hidden_run() as run:
                    SERVER._payload(run / ".content", "text")
                    raise RuntimeError("boom")
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_cli_timeout_is_actionable(self) -> None:
        with patch.object(
            SERVER.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(["lark-cli"], SERVER.CLI_TIMEOUT_SECONDS),
        ):
            with self.assertRaises(RuntimeError) as raised:
                SERVER._run_cli(["docs", "+fetch"], "pull test")
        error = json.loads(str(raised.exception))
        self.assertEqual(error["operation"], "pull test")
        self.assertEqual(error["error"], "timeout")

    def test_batch_failure_identifies_document(self) -> None:
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=RuntimeError("denied")):
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_pull(["doc-a"])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["failed_item"], "doc-a")
        self.assertEqual(error["completed"], 0)

    async def test_create_and_media_cleanup_payloads(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli:
            async with Client(SERVER.mcp) as client:
                await client.call_tool("create_document", {
                    "content": "# title", "parent_token": "folder-token",
                })
                await client.call_tool("insert_media", {
                    "doc": "doc-token", "filename": "pixel.png",
                    "content_base64": base64.b64encode(b"png").decode(),
                    "selection": "marker", "before": True,
                })
            self.assertEqual(run_cli.call_count, 2)
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_media_rejects_path_filename(self) -> None:
        with patch.object(SERVER, "_check_lark_cli"):
            with self.assertRaisesRegex(ValueError, "plain file name"):
                SERVER.insert_media("doc", "../secret", "eA==")

    async def test_whiteboard_update_cleans_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli:
            async with Client(SERVER.mcp) as client:
                await client.call_tool("whiteboard_update", {
                    "whiteboard_token": "board-token",
                    "source": "flowchart LR\nA --> B",
                })
            self.assertIn("--overwrite", run_cli.call_args.args[0])
            self.assertFalse(SERVER.WORKDIR.exists())

    async def test_whiteboard_query_retries_not_ready(self) -> None:
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=[RuntimeError("4003101"), {"ok": True}]) as run_cli, \
             patch.object(SERVER.time, "sleep"):
            async with Client(SERVER.mcp) as client:
                result = await client.call_tool("whiteboard_query", {
                    "whiteboard_token": "board-token",
                })
            self.assertTrue(result.data["ok"])
            self.assertEqual(run_cli.call_count, 2)

    def test_server_start_removes_stale_runs(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"):
            stale = SERVER.WORKDIR / ".run-stale"
            stale.mkdir(parents=True)
            (stale / ".content").write_text("old")
            SERVER._cleanup_stale_runs()
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_cleanup_preserves_only_state(self) -> None:
        cleanup_path = Path(__file__).with_name("cleanup_workspace.py")
        spec = importlib.util.spec_from_file_location("cleanup_workspace", cleanup_path)
        cleanup_module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(cleanup_module)
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / ".lark_publish"
            workdir.mkdir()
            (workdir / "state.json").write_text("{}")
            (workdir / "manifest.json").write_text("{}")
            (workdir / "markdown").mkdir()
            removed = cleanup_module.cleanup(workdir)
            self.assertEqual(removed, ["manifest.json", "markdown"])
            self.assertEqual([p.name for p in workdir.iterdir()], ["state.json"])

    def test_public_http_requires_authentication_and_tls(self) -> None:
        with patch.object(SERVER, "AUTH_PROVIDER", None):
            with self.assertRaisesRegex(RuntimeError, "configured authentication"):
                SERVER._https_config("0.0.0.0", None, None)

    def test_local_http_allows_no_tls(self) -> None:
        with patch.dict(SERVER.os.environ, {}, clear=True):
            self.assertEqual(SERVER._https_config("127.0.0.1", None, None), {})

    def test_https_requires_existing_certificates(self) -> None:
        with patch.object(SERVER, "AUTH_PROVIDER", object()):
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                SERVER._https_config("0.0.0.0", Path("missing.crt"), Path("missing.key"))

    def test_public_https_accepts_authentication_and_certificates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(SERVER, "AUTH_PROVIDER", object()):
            cert = Path(tmp) / "server.crt"
            key = Path(tmp) / "server.key"
            cert.touch()
            key.touch()
            self.assertEqual(
                SERVER._https_config("0.0.0.0", cert, key),
                {"ssl_certfile": str(cert), "ssl_keyfile": str(key)},
            )

    def test_auth_token_minimum_length(self) -> None:
        with patch.dict(SERVER.os.environ, {SERVER.TOKEN_ENV: "short"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "at least 32"):
                SERVER._auth_provider("token")

    def test_github_oauth_is_dcr_ready_and_user_limited(self) -> None:
        env = {
            SERVER.BASE_URL_ENV: "https://mcp.example.com",
            SERVER.GITHUB_CLIENT_ID_ENV: "client",
            SERVER.GITHUB_CLIENT_SECRET_ENV: "secret",
            SERVER.GITHUB_USER_ENV: "Charles",
        }
        with patch.dict(SERVER.os.environ, env, clear=True), \
             patch.object(SERVER, "GitHubProvider", return_value="provider") as provider:
            self.assertEqual(SERVER._auth_provider("github"), "provider")
            kwargs = provider.call_args.kwargs
            self.assertEqual(kwargs["base_url"], "https://mcp.example.com")
            self.assertIn("https://chatgpt.com/connector/oauth/*", kwargs["allowed_client_redirect_uris"])
            allowed = SERVER._authorized_github_user(SimpleNamespace(token=SimpleNamespace(
                claims={"login": "charles"},
            )))
        self.assertTrue(allowed)

    def test_github_oauth_rejects_non_https_origin(self) -> None:
        env = {
            SERVER.BASE_URL_ENV: "http://mcp.example.com/path",
            SERVER.GITHUB_CLIENT_ID_ENV: "client",
            SERVER.GITHUB_CLIENT_SECRET_ENV: "secret",
            SERVER.GITHUB_USER_ENV: "charles",
        }
        with patch.dict(SERVER.os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HTTPS origin"):
                SERVER._auth_provider("github")


if __name__ == "__main__":
    unittest.main()
