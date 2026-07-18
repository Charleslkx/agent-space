#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

MODULE_PATH = Path(__file__).with_name("mcp_server.py")
SPEC = importlib.util.spec_from_file_location("lark_publish_mcp", MODULE_PATH)
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
                "whiteboard_query", "whiteboard_update",
            },
        )

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

    def test_public_http_requires_token_and_tls(self) -> None:
        with patch.dict(SERVER.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "requires LARK_MCP_AUTH_TOKEN"):
                SERVER._https_config("0.0.0.0", None, None)

    def test_local_http_allows_no_tls(self) -> None:
        with patch.dict(SERVER.os.environ, {}, clear=True):
            self.assertEqual(SERVER._https_config("127.0.0.1", None, None), {})

    def test_https_requires_existing_certificates(self) -> None:
        with patch.dict(SERVER.os.environ, {SERVER.TOKEN_ENV: "x" * 32}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                SERVER._https_config("0.0.0.0", Path("missing.crt"), Path("missing.key"))

    def test_public_https_accepts_token_and_certificates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(SERVER.os.environ, {SERVER.TOKEN_ENV: "x" * 32}, clear=True):
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
                SERVER._auth_provider()


if __name__ == "__main__":
    unittest.main()
