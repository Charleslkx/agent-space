from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "server.py"


def load_server():
    environment = {
        "FIRECRAWL_MCP_BASE_URL": os.environ.get("FIRECRAWL_MCP_BASE_URL", "https://firecrawl.example.com"),
        "FIRECRAWL_MCP_GITHUB_CLIENT_ID": "client",
        "FIRECRAWL_MCP_GITHUB_CLIENT_SECRET": "secret",
        "FIRECRAWL_MCP_GITHUB_USERS": "Charles, AnotherUser",
        "FIRECRAWL_MCP_JWT_SIGNING_KEY": "a" * 64,
        "FIRECRAWL_MCP_STORAGE_KEY": FERNET_KEY,
        "FIRECRAWL_MCP_REDIS_PASSWORD": "redis-password",
        "FIRECRAWL_API_KEY": "firecrawl-key",
        "FIRECRAWL_MCP_UPDATE_CHECK": "0",
    }
    spec = importlib.util.spec_from_file_location("firecrawl_mcp_server", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    with patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


SERVER = load_server()


def write_fake_cli(directory: str, body: str) -> Path:
    fake = Path(directory) / "firecrawl"
    fake.write_text(f"#!/bin/sh\n{body}\n")
    fake.chmod(0o755)
    return fake


class ServerTest(unittest.TestCase):
    def test_whitelist_is_case_insensitive(self):
        token = type("Token", (), {"claims": {"login": "cHaRlEs"}})()
        context = type("Context", (), {"token": token})()
        with patch.dict(os.environ, {SERVER.GITHUB_USERS_ENV: "Charles, AnotherUser"}):
            self.assertTrue(SERVER._authorized_github_user(context))
        token.claims["login"] = "outside"
        with patch.dict(os.environ, {SERVER.GITHUB_USERS_ENV: "Charles"}):
            self.assertFalse(SERVER._authorized_github_user(context))

    def test_allowed_commands_pass(self):
        for args in (
            ["search", "query", "--limit", "5"],
            ["scrape", "https://example.com", "--format", "markdown"],
            ["map", "https://example.com", "--search", "auth"],
            ["crawl", "https://example.com/docs", "--limit", "10"],
            ["crawl", "job-id-123", "--status"],
            ["agent", "extract pricing", "--max-credits", "5"],
            ["research", "search-papers", "diffusion models", "--limit", "10"],
            ["credit-usage", "--json"],
            ["--status"],
            ["--help"],
        ):
            with self.subTest(args=args):
                SERVER._validate_args(args, None)

    def test_blocked_commands_are_rejected_with_a_reason(self):
        for args in (
            ["monitor", "list"],
            ["parse", "x.pdf"],
            ["login"],
            ["config", "show"],
            ["download", "https://example.com"],
            ["x", "download", "https://example.com"],
            ["experimental", "download", "https://example.com"],
            ["browser", "list"],
            ["interact", "run"],
            ["doctor"],
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    SERVER._validate_args(args, None)

    def test_blocked_flags_are_rejected_in_both_forms(self):
        forbidden = [
            ["scrape", "https://x", "-o", "out.md"],
            ["scrape", "https://x", "--output=out.md"],
            ["scrape", "https://x", "--api-key", "k"],
            ["scrape", "https://x", "--api-key=k"],
            ["-k", "k", "scrape", "https://x"],
            ["scrape", "https://x", "--schema-file", "s.json"],
            ["agent", "prompt", "--webhook", "https://evil.example"],
            ["crawl", "https://x", "--scrape-options-file", "s.json"],
            ["scrape", "https://x", "--profile", "shared"],
            ["scrape", "https://x", "--no-save-changes"],
        ]
        for args in forbidden:
            with self.subTest(args=args), self.assertRaises(ValueError):
                SERVER._validate_args(args, None)
        # inline JSON equivalents remain allowed
        SERVER._validate_args(["scrape", "https://x", "--schema", '{"a":1}'], None)

    def test_flag_values_are_not_mistaken_for_the_subcommand(self):
        SERVER._validate_args(["scrape", "--format", "map"], None)
        SERVER._validate_args(["crawl", "https://x", "--sitemap", "search"], None)

    def test_no_subcommand_requires_a_safe_bare_flag(self):
        with self.assertRaises(ValueError):
            SERVER._validate_args([], None)
        with self.assertRaises(ValueError):
            SERVER._validate_args(["-y"], None)

    def test_flags_may_not_precede_the_subcommand(self):
        # A root-level option taking a value would otherwise swallow the token
        # this validator reads as the command, and the CLI would run the next
        # one -- which is how a blocked command sneaks past an allowlist.
        for args in (
            ["--limit", "5", "search", "query"],
            ["--json", "monitor", "list"],
        ):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, "args\\[0\\] must be the subcommand"):
                    SERVER._validate_args(args, None)

    def test_concurrent_calls_are_capped_and_slots_are_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "echo ok")
            environment = {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }
            with patch.object(SERVER, "_CALL_SLOTS", threading.BoundedSemaphore(1)), \
                 patch.object(SERVER, "ACQUIRE_TIMEOUT_SECONDS", 0.05), \
                 patch.dict(os.environ, environment, clear=False):
                SERVER._CALL_SLOTS.acquire()
                with self.assertRaisesRegex(RuntimeError, "at capacity"):
                    SERVER.firecrawl_cli(["search", "query"])
                SERVER._CALL_SLOTS.release()
                # the slot is released even though the call raised
                self.assertEqual(SERVER.firecrawl_cli(["search", "q"])["stdout"], "ok\n")
                self.assertEqual(SERVER.firecrawl_cli(["search", "q"])["stdout"], "ok\n")

    def test_unspawnable_argv_becomes_a_clear_error(self):
        oversized = ["search", *(["x" * SERVER.MAX_ARG_BYTES] * (SERVER.MAX_ARGS - 1))]
        SERVER._validate_args(oversized, None)  # within the documented limits
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "echo ok")
            with patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                with self.assertRaisesRegex(RuntimeError, "could not run firecrawl"):
                    SERVER.firecrawl_cli(oversized)

    def test_tool_preserves_stdout_stderr_exit_code_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(
                directory,
                'printf \'out:%s\\n\' "$1"\nprintf \'in:\' >&2\ncat >&2\nexit 1\n',
            )
            with patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                result = SERVER.firecrawl_cli(["search"], "payload")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["stdout"], "out:search\n")
        self.assertEqual(result["stderr"], "in:payload")
        self.assertFalse(result["timed_out"])
        self.assertNotIn("update_available", result)

    def test_tool_reports_timeout_without_rewriting_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "printf before\nsleep 2\n")
            with patch.object(SERVER, "TIMEOUT_SECONDS", 0.01), patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                result = SERVER.firecrawl_cli(["crawl", "https://example.com"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertIn("hint", result)

    def test_tool_truncates_oversized_output_instead_of_discarding_it(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "yes x | head -c 11000000\n")
            with patch.object(SERVER, "MAX_OUTPUT_BYTES", 1024), patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                result = SERVER.firecrawl_cli(["search", "query"])
        self.assertTrue(result["truncated"])
        self.assertGreater(result["original_bytes"], 1024)
        self.assertTrue(result["stdout"])
        self.assertIn("hint", result)

    def test_error_stderr_is_passed_through_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(
                directory,
                'echo \'Error: Firecrawl request failed (HTTP 402)\' >&2\nexit 1\n',
            )
            with patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                result = SERVER.firecrawl_cli(["scrape", "https://example.com"])
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("HTTP 402", result["stderr"])

    def test_blocked_flag_message_names_the_alternative(self):
        with self.assertRaisesRegex(ValueError, "stdout"):
            SERVER._validate_args(["scrape", "https://x", "-o", "out.md"], None)

    def test_workdir_is_removed_after_the_call(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "pwd > /tmp/observed-workdir\n")
            with patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                SERVER.firecrawl_cli(["search", "query"])
            observed = Path("/tmp/observed-workdir").read_text().strip()
            Path("/tmp/observed-workdir").unlink()
        self.assertFalse(Path(observed).exists())

    def test_workdir_is_removed_even_on_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, "pwd > /tmp/observed-workdir-timeout\nsleep 2\n")
            with patch.object(SERVER, "TIMEOUT_SECONDS", 0.5), patch.dict(os.environ, {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "0",
            }, clear=False):
                SERVER.firecrawl_cli(["crawl", "https://example.com"])
            observed = Path("/tmp/observed-workdir-timeout").read_text().strip()
            Path("/tmp/observed-workdir-timeout").unlink()
        self.assertFalse(Path(observed).exists())

    def test_firecrawl_skill_returns_content_and_rejects_unknown_names(self):
        text = SERVER.firecrawl_skill("overview")
        self.assertIn("MCP boundary", text)
        self.assertEqual(SERVER.firecrawl_skill(), text)
        with self.assertRaises(ValueError):
            SERVER.firecrawl_skill("nonexistent")

    def test_auth_provider_requires_https_origin(self):
        with patch.dict(os.environ, {SERVER.BASE_URL_ENV: "http://bad.example/path"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "HTTPS origin"):
                SERVER._base_url()

    def test_workbuddy_callback_is_allowlisted(self):
        self.assertIn(SERVER.WORKBUDDY_REDIRECT_URI, SERVER.ALLOWED_CLIENT_REDIRECT_URIS)

    def test_update_available_reported_only_when_newer(self):
        SERVER._INSTALLED_VERSION = "1.19.27"
        try:
            with patch.dict(SERVER._LATEST_STATE, {"version": "1.19.28", "checked_at": time.time(), "checking": False}):
                self.assertEqual(SERVER._update_available(), "1.19.28")
            with patch.dict(SERVER._LATEST_STATE, {"version": "1.19.27", "checked_at": time.time(), "checking": False}):
                self.assertIsNone(SERVER._update_available())
            with patch.dict(SERVER._LATEST_STATE, {"version": None, "checked_at": time.time(), "checking": False}):
                self.assertIsNone(SERVER._update_available())
        finally:
            SERVER._INSTALLED_VERSION = SERVER._UNPROBED

    def test_version_is_probed_once_even_when_the_cli_reports_nothing(self):
        # A probe that yields no version used to be cached as None, which is
        # also the "not probed yet" sentinel -- so every later tool call paid
        # for another `firecrawl --version` subprocess.
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "invocations"
            log.write_text("")
            fake = write_fake_cli(directory, f'echo "$*" >> {log}\n[ "$1" = "--version" ] && exit 0\necho ok')
            environment = {
                "FIRECRAWL_MCP_CLI_PATH": str(fake),
                "FIRECRAWL_API_KEY": "firecrawl-key",
                "FIRECRAWL_MCP_UPDATE_CHECK": "1",
            }
            SERVER._INSTALLED_VERSION = SERVER._UNPROBED
            try:
                with patch.dict(os.environ, environment, clear=False), \
                     patch.dict(SERVER._LATEST_STATE,
                                {"version": "9.9.9", "checked_at": time.time(), "checking": False}):
                    for _ in range(4):
                        SERVER.firecrawl_cli(["search", "query"])
            finally:
                SERVER._INSTALLED_VERSION = SERVER._UNPROBED
            probes = [line for line in log.read_text().splitlines() if line.strip() == "--version"]
        self.assertEqual(len(probes), 1)

    def test_disabled_update_check_also_skips_the_version_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "invocations"
            log.write_text("")
            fake = write_fake_cli(directory, f'echo "$*" >> {log}\necho ok')
            SERVER._INSTALLED_VERSION = SERVER._UNPROBED
            try:
                with patch.dict(os.environ, {
                    "FIRECRAWL_MCP_CLI_PATH": str(fake),
                    "FIRECRAWL_API_KEY": "firecrawl-key",
                    "FIRECRAWL_MCP_UPDATE_CHECK": "0",
                }, clear=False):
                    SERVER.firecrawl_cli(["search", "query"])
            finally:
                SERVER._INSTALLED_VERSION = SERVER._UNPROBED
            invocations = [line for line in log.read_text().splitlines() if line]
        self.assertEqual(invocations, ["search query"])

    def test_update_check_failure_never_breaks_a_tool_call(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = write_fake_cli(directory, 'echo ok\n')
            with patch.object(SERVER, "_refresh_latest_version", side_effect=RuntimeError("boom")), \
                 patch.dict(os.environ, {
                     "FIRECRAWL_MCP_CLI_PATH": str(fake),
                     "FIRECRAWL_API_KEY": "firecrawl-key",
                     "FIRECRAWL_MCP_UPDATE_CHECK": "1",
                 }, clear=False), \
                 patch.dict(SERVER._LATEST_STATE, {"version": None, "checked_at": 0.0, "checking": False}):
                result = SERVER.firecrawl_cli(["search", "query"])
        self.assertEqual(result["stdout"], "ok\n")
        self.assertNotIn("update_available", result)


if __name__ == "__main__":
    unittest.main()
