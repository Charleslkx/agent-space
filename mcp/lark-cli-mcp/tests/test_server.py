from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "server.py"


def load_server():
    environment = {
        "LARK_CLI_MCP_BASE_URL": "https://lark.example.com",
        "LARK_CLI_MCP_GITHUB_CLIENT_ID": "client",
        "LARK_CLI_MCP_GITHUB_CLIENT_SECRET": "secret",
        "LARK_CLI_MCP_GITHUB_USERS": "Charles, AnotherUser",
        "LARK_CLI_MCP_JWT_SIGNING_KEY": "a" * 64,
        "LARK_CLI_MCP_STORAGE_KEY": FERNET_KEY,
        "LARK_CLI_MCP_REDIS_PASSWORD": "redis-password",
        "LARK_CLI_MCP_UPDATE_CHECK": "0",
    }
    spec = importlib.util.spec_from_file_location("lark_cli_mcp_server", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    with patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


SERVER = load_server()


def write_fake_cli(directory: str, body: str) -> Path:
    fake = Path(directory) / "lark-cli"
    fake.write_text(f"#!/bin/sh\n{body}\n")
    fake.chmod(0o755)
    return fake


class ServerTest(unittest.TestCase):
    def setUp(self):
        SERVER._INSTALLED_VERSION = None

    def test_tools_are_registered(self):
        import asyncio
        tools = asyncio.run(SERVER.mcp.list_tools(run_middleware=False))
        self.assertEqual({tool.name for tool in tools}, {"lark_cli", "lark_cli_skill"})

    def test_github_allowlist_is_case_insensitive(self):
        token = type("Token", (), {"claims": {"login": "cHaRlEs"}})()
        context = type("Context", (), {"token": token})()
        with patch.dict(os.environ, {SERVER.GITHUB_USERS_ENV: "Charles, AnotherUser"}):
            self.assertTrue(SERVER._authorized_github_user(context))
            token.claims["login"] = "outside"
            self.assertFalse(SERVER._authorized_github_user(context))

    def test_base_url_requires_lark_https_origin(self):
        for value in (
            "http://lark.example.com", "https://mcp.example.com", "https://lark.example.com/path",
            "https://lark.example.com:443", "https://user@lark.example.com", "https://lark.example.com?x=1",
        ):
            with self.subTest(value=value), patch.dict(os.environ, {SERVER.BASE_URL_ENV: value}):
                with self.assertRaises(RuntimeError):
                    SERVER._base_url()

    def test_business_commands_pass(self):
        for args in (
            ["calendar", "+agenda", "--as", "user"],
            ["api", "GET", "/open-apis/contact/v3/users"],
            ["schema", "calendar.calendar.event.list"],
            ["apps", "+list", "--as", "user"],
            ["event", "list"],
            ["event", "consume", "im.message.receive_v1", "--timeout", "30s"],
            ["--version"],
        ):
            with self.subTest(args=args):
                SERVER._validate_args(args, None)

    def test_server_management_commands_are_blocked(self):
        for args in (["auth", "status"], ["config", "show"], ["profile", "list"], ["update"], ["doctor"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SERVER._validate_args(args, None)

    def test_local_file_and_profile_flags_are_blocked(self):
        for args in (
            ["drive", "+upload", "--file", "x.pdf"],
            ["docs", "+fetch", "--output=doc.md"],
            ["whiteboard", "+query", "--output-dir", "x"],
            ["calendar", "+agenda", "--profile", "other"],
            ["api", "POST", "/x", "--data", "@payload.json"],
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SERVER._validate_args(args, None)
        SERVER._validate_args(["api", "POST", "/x", "--data", "@-"], "{}")

    def test_local_apps_commands_are_blocked(self):
        for subcommand in SERVER.BLOCKED_APPS_COMMANDS:
            with self.subTest(subcommand=subcommand), self.assertRaises(ValueError):
                SERVER._validate_args(["apps", subcommand], None)

    def test_event_consumer_must_be_bounded(self):
        with self.assertRaisesRegex(ValueError, "requires --timeout"):
            SERVER._validate_args(["event", "consume", "im.message.receive_v1"], None)
        with self.assertRaisesRegex(ValueError, "no greater than 150s"):
            SERVER._validate_args(["event", "consume", "im.message.receive_v1", "--timeout", "3m"], None)
        with self.assertRaises(ValueError):
            SERVER._validate_args(["event", "stop"], None)

    def test_tool_preserves_cli_output_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, 'printf "out:%s\\n" "$1"\nprintf "in:" >&2\ncat >&2\nexit 10')
            with patch.dict(os.environ, {
                SERVER.CLI_ENV: str(fake), SERVER.UPDATE_CHECK_ENV: "0", SERVER.STATE_DIR_ENV: directory,
            }, clear=False):
                result = SERVER.lark_cli(["calendar"], "payload")
        self.assertEqual(result["exit_code"], 10)
        self.assertEqual(result["stdout"], "out:calendar\n")
        self.assertEqual(result["stderr"], "in:payload")
        self.assertNotIn("update_available", result)

    def test_timeout_is_returned_as_data(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "printf before\nsleep 2")
            with patch.object(SERVER, "TIMEOUT_SECONDS", 0.01), patch.dict(os.environ, {
                SERVER.CLI_ENV: str(fake), SERVER.UPDATE_CHECK_ENV: "0", SERVER.STATE_DIR_ENV: directory,
            }, clear=False):
                result = SERVER.lark_cli(["calendar", "+agenda"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])

    def test_oversized_output_is_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "printf 1234567890")
            with patch.object(SERVER, "MAX_OUTPUT_BYTES", 5), patch.dict(os.environ, {
                SERVER.CLI_ENV: str(fake), SERVER.UPDATE_CHECK_ENV: "0", SERVER.STATE_DIR_ENV: directory,
            }, clear=False):
                result = SERVER.lark_cli(["calendar", "+agenda"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stdout"], "12345")

    def test_child_environment_does_not_expose_server_secrets(self):
        with patch.dict(os.environ, {"LARK_CLI_MCP_GITHUB_CLIENT_SECRET": "do-not-pass"}, clear=False):
            child = SERVER._child_env("/tmp/work")
        self.assertNotIn("LARK_CLI_MCP_GITHUB_CLIENT_SECRET", child)
        self.assertEqual(child["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"], "1")

    def test_skill_list_and_read_use_embedded_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, 'printf \'{"ok":true,"path":"%s"}\' "$3"')
            with patch.dict(os.environ, {
                SERVER.CLI_ENV: str(fake), SERVER.UPDATE_CHECK_ENV: "0", SERVER.STATE_DIR_ENV: directory,
            }, clear=False):
                listed = SERVER.lark_cli_skill("list")
                read = SERVER.lark_cli_skill("read", "lark-doc/references/fetch.md")
        self.assertTrue(listed["result"]["ok"])
        self.assertEqual(read["result"]["path"], "lark-doc/references/fetch.md")

    def test_skill_path_traversal_is_rejected(self):
        for path in ("../secret", "/etc/passwd", "lark-doc/../secret", "lark-doc//file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SERVER._validate_skill_path(path, required=True)
        with self.assertRaises(ValueError):
            SERVER.lark_cli_skill("read")
        with self.assertRaises(ValueError):
            SERVER.lark_cli_skill("dump", "lark-doc")

    def test_update_info_only_when_newer(self):
        SERVER._INSTALLED_VERSION = "1.0.81"
        with patch.dict(SERVER._LATEST_STATE, {"version": "1.0.82", "checked_at": time.time(), "checking": False}):
            self.assertEqual(SERVER._update_available(), {
                "current_version": "1.0.81",
                "latest_version": "1.0.82",
                "upgrade_command": "sudo scripts/update-cli.sh 1.0.82",
            })
        with patch.dict(SERVER._LATEST_STATE, {"version": "1.0.81", "checked_at": time.time(), "checking": False}):
            self.assertIsNone(SERVER._update_available())

    def test_update_check_starts_once_and_never_blocks(self):
        class FakeThread:
            starts = 0
            def __init__(self, target, daemon):
                self.target = target
            def start(self):
                FakeThread.starts += 1

        with patch.dict(os.environ, {SERVER.UPDATE_CHECK_ENV: "1"}), \
             patch.object(SERVER.threading, "Thread", FakeThread), \
             patch.dict(SERVER._LATEST_STATE, {"version": None, "checked_at": 0.0, "checking": False}):
            SERVER._maybe_start_update_check()
            SERVER._maybe_start_update_check()
            self.assertEqual(FakeThread.starts, 1)

    def test_update_check_failure_is_swallowed(self):
        with patch.object(SERVER.urllib.request, "urlopen", side_effect=OSError("offline")), \
             patch.dict(SERVER._LATEST_STATE, {"version": None, "checked_at": 0.0, "checking": True}):
            SERVER._refresh_latest_version()
            self.assertIsNone(SERVER._LATEST_STATE["version"])
            self.assertFalse(SERVER._LATEST_STATE["checking"])

    def test_disabled_update_check_starts_no_thread(self):
        with patch.dict(os.environ, {SERVER.UPDATE_CHECK_ENV: "0"}), \
             patch.object(SERVER.threading, "Thread") as thread:
            SERVER._maybe_start_update_check()
            thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
