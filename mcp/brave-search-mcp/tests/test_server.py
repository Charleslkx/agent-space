from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "server.py"


def load_server():
    environment = {
        "BRAVE_MCP_BASE_URL": os.environ.get("BRAVE_MCP_BASE_URL", "https://brave.example.com"),
        "BRAVE_MCP_GITHUB_CLIENT_ID": "client",
        "BRAVE_MCP_GITHUB_CLIENT_SECRET": "secret",
        "BRAVE_MCP_GITHUB_USERS": "Charles, AnotherUser",
        "BRAVE_MCP_JWT_SIGNING_KEY": "a" * 64,
        "BRAVE_MCP_STORAGE_KEY": FERNET_KEY,
        "BRAVE_MCP_REDIS_PASSWORD": "redis-password",
        "BRAVE_SEARCH_API_KEY": "brave-key",
    }
    spec = importlib.util.spec_from_file_location("brave_search_server", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    with patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


SERVER = load_server()


class ServerTest(unittest.TestCase):
    def test_whitelist_is_case_insensitive(self):
        token = type("Token", (), {"claims": {"login": "cHaRlEs"}})()
        context = type("Context", (), {"token": token})()
        with patch.dict(os.environ, {SERVER.GITHUB_USERS_ENV: "Charles, AnotherUser"}):
            self.assertTrue(SERVER._authorized_github_user(context))
        token.claims["login"] = "outside"
        with patch.dict(os.environ, {SERVER.GITHUB_USERS_ENV: "Charles"}):
            self.assertFalse(SERVER._authorized_github_user(context))

    def test_search_boundary_rejects_server_configuration_and_files(self):
        forbidden = [
            ["config", "show"],
            ["--config", "/tmp/x", "web", "query"],
            ["--api-key=x", "web", "query"],
            ["--base-url", "http://127.0.0.1", "web", "query"],
            ["web", "query", "--goggles", "@/etc/passwd"],
            ["web", "query", "--goggles=@rules"],
        ]
        for args in forbidden:
            with self.subTest(args=args), self.assertRaises(ValueError):
                SERVER._validate_args(args, None)
        SERVER._validate_args(["web", "query", "--goggles", "@-"], "rule")

    def test_only_the_four_in_plan_commands_are_allowed(self):
        for args in (
            ["web", "query", "--count", "5"],
            ["news", "query", "--freshness", "pd"],
            ["images", "query"],
            ["videos", "query"],
            ["--help"],
            ["--version"],
        ):
            with self.subTest(args=args):
                SERVER._validate_args(args, None)
        for args in (
            ["context", "query"],       # not in this deployment's plan
            ["answers", "query"],
            ["spellcheck", "query"],
            ["bare query"],             # bx would route this to context
            [],
            ["--count", "5", "web", "query"],   # flags may not precede the command
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SERVER._validate_args(args, None)

    def test_oversized_output_is_truncated_not_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bx = Path(directory) / "bx"
            fake_bx.write_text("#!/bin/sh\nyes x | head -c 9000\n")
            fake_bx.chmod(0o755)
            with patch.object(SERVER, "MAX_OUTPUT_BYTES", 1024), patch.dict(os.environ, {
                "BRAVE_MCP_BX_PATH": str(fake_bx),
                "BRAVE_SEARCH_API_KEY": "brave-key",
            }, clear=False):
                result = SERVER.brave_search_cli(["web", "query"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["original_bytes"], 9000)
        self.assertEqual(len(result["stdout"]), 1024)
        self.assertIn("hint", result)

    def test_concurrent_calls_are_capped_and_slots_are_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bx = Path(directory) / "bx"
            fake_bx.write_text("#!/bin/sh\necho ok\n")
            fake_bx.chmod(0o755)
            environment = {"BRAVE_MCP_BX_PATH": str(fake_bx), "BRAVE_SEARCH_API_KEY": "brave-key"}
            with patch.object(SERVER, "_CALL_SLOTS", threading.BoundedSemaphore(1)), \
                 patch.object(SERVER, "ACQUIRE_TIMEOUT_SECONDS", 0.05), \
                 patch.dict(os.environ, environment, clear=False):
                SERVER._CALL_SLOTS.acquire()
                with self.assertRaisesRegex(RuntimeError, "at capacity"):
                    SERVER.brave_search_cli(["web", "query"])
                SERVER._CALL_SLOTS.release()
                # the slot is released even though the call raised
                self.assertEqual(SERVER.brave_search_cli(["web", "query"])["stdout"], "ok\n")
                self.assertEqual(SERVER.brave_search_cli(["web", "query"])["stdout"], "ok\n")

    def test_unspawnable_argv_becomes_a_clear_error(self):
        oversized = ["web", *(["x" * SERVER.MAX_ARG_BYTES] * (SERVER.MAX_ARGS - 1))]
        SERVER._validate_args(oversized, None)  # within the documented limits
        with tempfile.TemporaryDirectory() as directory:
            fake_bx = Path(directory) / "bx"
            fake_bx.write_text("#!/bin/sh\necho ok\n")
            fake_bx.chmod(0o755)
            with patch.dict(os.environ, {
                "BRAVE_MCP_BX_PATH": str(fake_bx),
                "BRAVE_SEARCH_API_KEY": "brave-key",
            }, clear=False):
                with self.assertRaisesRegex(RuntimeError, "could not run bx"):
                    SERVER.brave_search_cli(oversized)

    def test_tool_preserves_stdout_stderr_exit_code_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bx = Path(directory) / "bx"
            fake_bx.write_text("#!/bin/sh\nprintf 'out:%s\\n' \"$1\"\nprintf 'in:' >&2\ncat >&2\nexit 7\n")
            fake_bx.chmod(0o755)
            with patch.dict(os.environ, {
                "BRAVE_MCP_BX_PATH": str(fake_bx),
                "BRAVE_SEARCH_API_KEY": "brave-key",
            }, clear=False):
                result = SERVER.brave_search_cli(["web"], "payload")
        self.assertEqual(result, {
            "exit_code": 7,
            "stdout": "out:web\n",
            "stderr": "in:payload",
            "timed_out": False,
        })

    def test_tool_reports_timeout_without_rewriting_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bx = Path(directory) / "bx"
            fake_bx.write_text("#!/bin/sh\nprintf before\nsleep 2\n")
            fake_bx.chmod(0o755)
            with patch.object(SERVER, "TIMEOUT_SECONDS", 0.01), patch.dict(os.environ, {
                "BRAVE_MCP_BX_PATH": str(fake_bx),
                "BRAVE_SEARCH_API_KEY": "brave-key",
            }, clear=False):
                result = SERVER.brave_search_cli(["web"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])

    def test_auth_provider_requires_https_origin(self):
        with patch.dict(os.environ, {SERVER.BASE_URL_ENV: "http://bad.example/path"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "HTTPS origin"):
                SERVER._base_url()

    def test_workbuddy_callback_is_allowlisted(self):
        self.assertIn(SERVER.WORKBUDDY_REDIRECT_URI, SERVER.ALLOWED_CLIENT_REDIRECT_URIS)


if __name__ == "__main__":
    unittest.main()
