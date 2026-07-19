#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import Client

MODULE_PATH = Path(__file__).with_name("mcp_server.py")
SPEC = importlib.util.spec_from_file_location("lark_markdown", MODULE_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SERVER)


class MCPServerTest(unittest.IsolatedAsyncioTestCase):
    def test_server_name_is_canonical(self) -> None:
        self.assertEqual(SERVER.mcp.name, "Lark-Markdown")
        self.assertEqual(SERVER.mcp.version, "0.12.0")
        self.assertIn("never configure or start a server", SERVER.mcp.instructions.lower())

    async def test_tools_are_registered(self) -> None:
        async with Client(SERVER.mcp) as client:
            tools = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "check_lark_cli", "begin_lark_auth", "complete_lark_auth", "schedule_mcp_restart", "batch_pull", "batch_push", "point_update",
                "create_document", "create_wiki_node", "create_wiki_space", "insert_media", "whiteboard_query", "whiteboard_update",
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

    def test_batch_push_validates_every_item_before_writing(self) -> None:
        with patch.object(SERVER, "_check_lark_cli") as check, \
             patch.object(SERVER, "_run_cli") as run_cli:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_push([
                    {"doc": "doc-a", "content": "valid"},
                    {"doc": "doc-b", "content": "invalid", "mode": "unknown"},
                ])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["failed_index"], 1)
        self.assertEqual(error["completed"], 0)
        check.assert_not_called()
        run_cli.assert_not_called()

    def test_lark_cli_success_shape(self) -> None:
        auth = {
            "identity": "user",
            "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        completed = subprocess.CompletedProcess([], 0, "lark-cli version 1.0.69", "")
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", return_value=auth):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertEqual(status["user_status"], "ready")
        self.assertTrue(status["verified"])

    def test_lark_cli_update_notice_is_non_blocking(self) -> None:
        auth = {
            "identity": "user", "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
            "_notice": {"update": {"latest": "9.9.9", "command": "lark-cli update"}},
        }
        completed = subprocess.CompletedProcess([], 0, "lark-cli version 1.0.69", "")
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", return_value=auth):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertTrue(status["verified"])
        self.assertEqual(status["update_notice"]["latest"], "9.9.9")

    def test_lark_cli_version_failure_is_non_blocking(self) -> None:
        auth = {
            "identity": "user", "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER, "_run_process", side_effect=SERVER.LarkCLIError({
                 "operation": "check lark-cli version", "error": "timeout",
             })), patch.object(SERVER, "_run_cli", return_value=auth):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertTrue(status["verified"])
        self.assertIsNone(status["version"])
        self.assertEqual(status["version_warning"]["error"], "timeout")

    def test_lark_cli_retries_transient_auth_failure(self) -> None:
        unavailable = {"verified": False, "identities": {"user": {}}}
        ready = {
            "identity": "user",
            "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        completed = subprocess.CompletedProcess([], 0, "lark-cli version 1.0.69", "")
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", side_effect=[unavailable, ready]), \
             patch.object(SERVER.time, "sleep"):
            status = SERVER._check_lark_cli(use_cache=False)
        self.assertTrue(status["verified"])

    def test_begin_lark_auth_returns_link_device_code_and_qr(self) -> None:
        payload = {
            "data": {
                "verification_url": "https://auth.example/verify",
                "device_code": "device-code",
            }
        }
        completed = type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_run_cli", return_value=payload) as run_cli, \
             patch.object(SERVER.subprocess, "run", return_value=completed) as run:
            def write_qr(*args, **kwargs):
                output = Path(args[0][args[0].index("--output") + 1])
                output.write_bytes(b"png")
                return completed
            run.side_effect = write_qr
            result = SERVER.begin_lark_auth()
        self.assertEqual(result["verification_url"], "https://auth.example/verify")
        self.assertEqual(result["device_code"], "device-code")
        self.assertEqual(base64.b64decode(result["qr_code_png_base64"]), b"png")
        self.assertIn("wiki", run_cli.call_args.args[0])
        self.assertFalse((Path(tmp) / ".lark_publish").exists())

    def test_complete_lark_auth_uses_device_code(self) -> None:
        with patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli, \
             patch.object(SERVER, "_check_lark_cli", return_value={"verified": True}):
            self.assertEqual(SERVER.complete_lark_auth("device-code"), {"verified": True})
        self.assertEqual(
            run_cli.call_args.args[0], ["auth", "login", "--device-code", "device-code"],
        )

    def test_payload_uses_project_relative_cli_path(self) -> None:
        workdir = Path(".lark_publish-relative-payload-test")
        try:
            with patch.object(SERVER, "WORKDIR", workdir):
                with SERVER._hidden_run() as run:
                    payload = SERVER._payload(run / ".content", "body")
                    self.assertTrue(payload.startswith("@./.lark_publish-relative-payload-test/"))
        finally:
            if workdir.exists():
                import shutil
                shutil.rmtree(workdir)

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

    def test_schedule_mcp_restart_requires_confirmation_and_valid_delay(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmation"):
            SERVER.schedule_mcp_restart("restart")
        with self.assertRaisesRegex(ValueError, "between 5 and 300"):
            SERVER.schedule_mcp_restart(SERVER.RESTART_CONFIRMATION, 4)

    def test_schedule_mcp_restart_starts_fixed_worker(self) -> None:
        worker = SimpleNamespace(pid=4321)
        with patch.object(SERVER.subprocess, "Popen", return_value=worker) as popen:
            result = SERVER.schedule_mcp_restart(SERVER.RESTART_CONFIRMATION, 12)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["service"], "lark-markdown-mcp.service")
        self.assertEqual(result["delay_seconds"], 12)
        self.assertEqual(result["worker_pid"], 4321)
        self.assertEqual(
            popen.call_args.args[0],
            [sys.executable, str(SERVER.RESTART_WORKER), "--delay", "12"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], SERVER.PROJECT_ROOT)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_cli_runs_from_skill_root(self) -> None:
        completed = subprocess.CompletedProcess(["lark-cli"], 0, "{}", "")
        with patch.object(SERVER.subprocess, "run", return_value=completed) as run:
            SERVER._run_cli(["docs", "+media-insert"], "insert media")
        self.assertEqual(run.call_args.kwargs["cwd"], SERVER.PROJECT_ROOT)

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
            media_args = run_cli.call_args_list[1].args[0]
            media_path = media_args[media_args.index("--file") + 1]
            self.assertTrue(media_path.startswith("./"))
            self.assertFalse(Path(media_path).is_absolute())
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_create_wiki_node_uses_explicit_parent_or_space(self) -> None:
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli:
            SERVER.create_wiki_node("Child", parent_node_token="parent-token")
        self.assertEqual(run_cli.call_args.args[0], [
            "wiki", "+node-create", "--as", "user", "--title", "Child", "--obj-type", "docx", "--format", "json",
            "--parent-node-token", "parent-token",
        ])

    def test_create_wiki_node_requires_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "parent_node_token or space_id"):
            SERVER.create_wiki_node("Unplaced")

    def test_create_wiki_space_passes_description(self) -> None:
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value={"ok": True}) as run_cli:
            SERVER.create_wiki_space("Team wiki", "Shared notes")
        self.assertEqual(run_cli.call_args.args[0], [
            "wiki", "+space-create", "--as", "user", "--name", "Team wiki", "--format", "json",
            "--description", "Shared notes",
        ])

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

    def test_auth_token_file_requires_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("x" * 64)
            token_file.chmod(0o644)
            with patch.dict(SERVER.os.environ, {
                SERVER.TOKEN_FILE_ENV: str(token_file),
            }, clear=True), self.assertRaisesRegex(RuntimeError, "0600"):
                SERVER._auth_provider("token")
            token_file.chmod(0o600)
            with patch.dict(SERVER.os.environ, {
                SERVER.TOKEN_FILE_ENV: str(token_file),
            }, clear=True):
                self.assertIsNotNone(SERVER._auth_provider("token"))

    def test_secret_manager_refuses_non_interactive_use(self) -> None:
        script = Path(__file__).with_name("manage_secret_key.py")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "token"
            result = subprocess.run(
                [sys.executable, str(script), "init", str(target)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing non-interactive use", result.stderr)
            self.assertFalse(target.exists())

    def test_secret_manager_writes_and_rotates_private_file(self) -> None:
        script = Path(__file__).with_name("manage_secret_key.py")
        spec = importlib.util.spec_from_file_location("manage_secret_key", script)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "token"
            module._write(target, replace=False)
            original = target.read_text().strip()
            self.assertGreaterEqual(len(original), 32)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            module._write(target, replace=True)
            self.assertNotEqual(target.read_text().strip(), original)

    def test_github_oauth_is_dcr_ready_and_user_limited(self) -> None:
        env = {
            SERVER.BASE_URL_ENV: "https://mcp.example.com",
            SERVER.GITHUB_CLIENT_ID_ENV: "client",
            SERVER.GITHUB_CLIENT_SECRET_ENV: "secret",
            SERVER.GITHUB_JWT_SIGNING_KEY_ENV: "stable-signing-key",
            SERVER.GITHUB_USER_ENV: "Charles",
        }
        with patch.dict(SERVER.os.environ, env, clear=True), \
             patch.object(SERVER, "_OriginCompatibleGitHubProvider", return_value="provider") as provider:
            self.assertEqual(SERVER._auth_provider("github"), "provider")
            kwargs = provider.call_args.kwargs
            self.assertEqual(kwargs["base_url"], "https://mcp.example.com")
            self.assertIn("https://chatgpt.com/connector/oauth/*", kwargs["allowed_client_redirect_uris"])
            self.assertIn("https://claude.ai/api/mcp/auth_callback", kwargs["allowed_client_redirect_uris"])
            self.assertIn("http://localhost:*", kwargs["allowed_client_redirect_uris"])
            self.assertIn("http://127.0.0.1:*", kwargs["allowed_client_redirect_uris"])
            allowed = SERVER._authorized_github_user(SimpleNamespace(token=SimpleNamespace(
                claims={"login": "charles"},
            )))
        self.assertTrue(allowed)

    def test_github_oauth_rejects_non_https_origin(self) -> None:
        env = {
            SERVER.BASE_URL_ENV: "http://mcp.example.com/path",
            SERVER.GITHUB_CLIENT_ID_ENV: "client",
            SERVER.GITHUB_CLIENT_SECRET_ENV: "secret",
            SERVER.GITHUB_JWT_SIGNING_KEY_ENV: "stable-signing-key",
            SERVER.GITHUB_USER_ENV: "charles",
        }
        with patch.dict(SERVER.os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HTTPS origin"):
                SERVER._auth_provider("github")

    async def test_claude_code_uses_fixed_local_oauth_client(self) -> None:
        provider = object.__new__(SERVER._OriginCompatibleGitHubProvider)
        provider._client_store = SimpleNamespace(
            get=AsyncMock(return_value=None), put=AsyncMock(),
        )

        client = await provider.get_client(SERVER.CLAUDE_CODE_CLIENT_ID)

        self.assertEqual(client.client_id, SERVER.CLAUDE_CODE_CLIENT_ID)
        self.assertEqual(client.token_endpoint_auth_method, "none")
        self.assertEqual(
            str(client.validate_redirect_uri("http://localhost:57569/callback")),
            "http://localhost:57569/callback",
        )
        provider._client_store.put.assert_awaited_once_with(
            key=SERVER.CLAUDE_CODE_CLIENT_ID, value=client,
        )

        client.allowed_redirect_uri_patterns = ["http://localhost:3118/callback"]
        provider._client_store.get.return_value = client
        client = await provider.get_client(SERVER.CLAUDE_CODE_CLIENT_ID)
        self.assertEqual(client.allowed_redirect_uri_patterns, [SERVER.CLAUDE_CODE_REDIRECT_URI_PATTERN])
        self.assertEqual(provider._client_store.put.await_count, 2)


if __name__ == "__main__":
    unittest.main()
