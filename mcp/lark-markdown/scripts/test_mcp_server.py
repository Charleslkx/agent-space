#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastmcp import Client

MODULE_PATH = Path(__file__).with_name("mcp_server.py")
SPEC = importlib.util.spec_from_file_location("lark_markdown", MODULE_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SERVER)


class MCPServerTest(unittest.IsolatedAsyncioTestCase):
    def test_server_name_is_canonical(self) -> None:
        self.assertEqual(SERVER.mcp.name, "Lark-Markdown")
        self.assertEqual(SERVER.mcp.version, "0.16.0")
        instructions = SERVER.mcp.instructions.lower()
        self.assertIn("never configure or start a server", instructions)
        self.assertIn("if it finds multiple targets", instructions)
        self.assertIn("use batch_pull only", instructions)
        self.assertIn("after point_update, verify with find_document_text", instructions)

    async def test_tools_are_registered(self) -> None:
        async with Client(SERVER.mcp) as client:
            tools = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "check_lark_cli", "begin_lark_app_setup", "complete_lark_app_setup", "begin_lark_auth", "complete_lark_auth", "schedule_mcp_restart", "batch_pull", "find_document_text", "search_documents", "batch_push", "point_update", "batch_point_update",
                "create_document", "create_wiki_node", "create_wiki_space", "scan_document_assets", "insert_media", "whiteboard_query", "whiteboard_update",
            },
        )
        self.assertTrue(all(tool.title for tool in tools))
        self.assertTrue(all(tool.outputSchema for tool in tools))
        self.assertTrue(all(
            tool.meta["securitySchemes"] == SERVER._security_schemes(SERVER.AUTH_MODE)
            for tool in tools
        ))
        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertIn("concurrency", schemas["batch_pull"]["properties"])
        self.assertIn("concurrency", schemas["batch_push"]["properties"])
        self.assertIn("context_chars", schemas["find_document_text"]["properties"])
        self.assertIn("doc_types", schemas["search_documents"]["properties"])
        self.assertIn("expected_revision_id", schemas["batch_point_update"]["properties"])

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
            self.assertEqual(list(SERVER.WORKDIR.glob(".run-*")), [])

    def test_center_display_math_skips_fenced_code(self) -> None:
        content = "$$x < y$$\n\n```text\n$$literal$$\n```\n"
        self.assertEqual(
            SERVER._center_display_math(content),
            '<p align="center"><latex>x &lt; y</latex></p>\n\n```text\n$$literal$$\n```\n',
        )

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

    def test_batch_push_runs_independent_documents_concurrently(self) -> None:
        barrier = threading.Barrier(2)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=lambda *_: (barrier.wait(timeout=1), {"ok": True})[1]):
            result = SERVER.batch_push([
                {"doc": "doc-a", "content": "A"},
                {"doc": "doc-b", "content": "B"},
            ], concurrency=2)
        self.assertEqual([item["doc"] for item in result], ["doc-a", "doc-b"])

    def test_batch_pull_runs_independent_documents_concurrently(self) -> None:
        barrier = threading.Barrier(2)
        response = {"data": {"document": {"revision_id": "1", "content": "body"}}}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=lambda *_: (barrier.wait(timeout=1), response)[1]):
            result = SERVER.batch_pull(["doc-a", "doc-b"], concurrency=2)
        self.assertEqual([item["doc"] for item in result], ["doc-a", "doc-b"])

    def test_find_document_text_returns_bounded_context_only(self) -> None:
        response = {"data": {"document": {
            "revision_id": "7",
            "content": "prefix target first context target second suffix",
        }}}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value=response):
            result = SERVER.find_document_text("doc-a", "target", context_chars=4, max_matches=1)
        self.assertEqual(result["revision_id"], "7")
        self.assertEqual(result["match_count"], 2)
        self.assertTrue(result["matches_truncated"])
        self.assertEqual(result["matches"], [{
            "start": 7, "end": 13, "before": "fix ", "match": "target", "after": " fir",
        }])
        self.assertNotIn("content", result)

    def test_search_documents_cleans_highlights_and_validates_count(self) -> None:
        response = {"data": {"results": [{
            "entity_type": "DOCX",
            "result_meta": {
                "token": "doc-a", "url": "https://example.feishu.cn/docx/doc-a",
                "owner_name": "Alice", "update_time_iso": "2026-07-01T00:00:00+08:00",
            },
            "title_highlighted": "<h>RAG</h> design",
            "summary_highlighted": "retrieval over <h>RAG</h> pipelines",
        }]}}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value=response) as run_cli:
            result = SERVER.search_documents("RAG", doc_types="docx", count=5)
        self.assertEqual(result, [{
            "doc": "doc-a", "url": "https://example.feishu.cn/docx/doc-a",
            "entity_type": "DOCX", "title": "**RAG** design",
            "snippet": "retrieval over **RAG** pipelines",
            "owner_name": "Alice", "update_time_iso": "2026-07-01T00:00:00+08:00",
        }])
        args = run_cli.call_args[0][0]
        self.assertIn("--doc-types", args)
        self.assertIn("docx", args)
        with self.assertRaisesRegex(ValueError, "count must be"):
            SERVER.search_documents("RAG", count=21)

    def test_point_update_rejects_ambiguous_text_before_writing(self) -> None:
        response = {"data": {"document": {"revision_id": 7, "content": "old old"}}}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value=response) as run_cli:
            with self.assertRaisesRegex(ValueError, "exactly once, found 2"):
                SERVER.point_update("doc-a", "old", "new")
        self.assertEqual(run_cli.call_count, 1)

    def test_point_update_writes_against_fetched_revision(self) -> None:
        responses = [
            {"data": {"document": {"revision_id": 7, "content": "old"}}},
            {"data": {"document": {"revision_id": 8}}},
        ]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=responses) as run_cli:
            SERVER.point_update("doc-a", "old", "new")
        args = run_cli.call_args_list[1].args[0]
        self.assertEqual(args[args.index("--revision-id") + 1], "7")

    def test_batch_point_update_preflights_all_updates_before_writing(self) -> None:
        document = {"revision_id": 7, "content": "first missing third"}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_write_exact_text") as write:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_point_update("doc-a", [
                    {"pattern": "first", "replacement": "missing"},
                    {"pattern": "missing", "replacement": "two"},
                    {"pattern": "third", "replacement": "three"},
                ])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "preflight_conflict")
        self.assertEqual(error["failed_index"], 1)
        self.assertEqual(error["completed"], 0)
        self.assertEqual(error["applied_indexes"], [])
        write.assert_not_called()

    def test_batch_point_update_chains_revisions(self) -> None:
        document = {"revision_id": "7", "content": "first second"}
        writes = [
            {"data": {"document": {"revision_id": 8}}},
            {"data": {"document": {"revision_id": 9}}},
        ]
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document) as fetch, \
             patch.object(SERVER, "_write_exact_text", side_effect=writes) as write:
            result = SERVER.batch_point_update("doc-a", [
                {"pattern": "first", "replacement": "one"},
                {"pattern": "second", "replacement": "two"},
            ], expected_revision_id=7)
        fetch.assert_called_once()
        self.assertEqual(len(result), 2)
        self.assertEqual(write.call_args_list[0].args[-1], 7)
        self.assertEqual(write.call_args_list[1].args[-1], 8)

    def test_batch_point_update_simulates_the_rendered_replacement(self) -> None:
        # Display math is rewritten on the way out, so a later pattern must be
        # preflighted against the rendered text, not the raw "$$...$$" input.
        document = {"revision_id": 7, "content": "PLACEHOLDER\ntail"}
        writes = [
            {"data": {"document": {"revision_id": 8}}},
            {"data": {"document": {"revision_id": 9}}},
        ]
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_write_exact_text", side_effect=writes) as write:
            SERVER.batch_point_update("doc-a", [
                {"pattern": "PLACEHOLDER", "replacement": "$$E=mc^2$$"},
                {"pattern": "<latex>E=mc^2</latex>", "replacement": "done"},
            ])
        self.assertEqual(write.call_count, 2)
        rendered = write.call_args_list[0].args[2]
        self.assertEqual(rendered, '<p align="center"><latex>E=mc^2</latex></p>')

    def test_point_update_writes_the_rendered_replacement(self) -> None:
        responses = [
            {"data": {"document": {"revision_id": 7, "content": "old"}}},
            {"data": {"document": {"revision_id": 8}}},
        ]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", side_effect=responses), \
             patch.object(SERVER, "_payload", return_value="@./payload") as payload:
            SERVER.point_update("doc-a", "old", "$$E=mc^2$$")
        self.assertEqual(payload.call_args.args[1], '<p align="center"><latex>E=mc^2</latex></p>')

    def test_point_update_rejects_oversized_pattern(self) -> None:
        with self.assertRaisesRegex(ValueError, "pattern exceeds"):
            SERVER.point_update("doc-a", "x" * (SERVER.MAX_PATTERN_BYTES + 1), "new")

    def test_point_update_raises_on_lark_failed_write(self) -> None:
        document = {"revision_id": 7, "content": "old target old"}
        failed = {
            "ok": True,
            "data": {
                "document": {"revision_id": 7},
                "result": "failed",
                "warnings": ["degrade_code=1013,msg=str_replace pattern was not found in the document"],
            },
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_run_cli", return_value=failed) as run_cli:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.point_update("doc-a", "target", "new")
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "lark_write_failed")
        self.assertIn("degrade_code=1013", error["message"])
        self.assertIn("document_state", error)
        self.assertEqual(run_cli.call_count, 1)

    def test_batch_point_update_stops_and_assesses_damage_on_failed_write(self) -> None:
        document = {"revision_id": 7, "content": "first second third"}
        writes = [
            {"ok": True, "data": {"document": {"revision_id": 8}, "result": "success", "warnings": []}},
            {"ok": True, "data": {"document": {"revision_id": 8}, "result": "failed",
             "warnings": ["degrade_code=1013,msg=str_replace pattern was not found"]}},
        ]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_run_cli", side_effect=writes) as run_cli:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_point_update("doc-a", [
                    {"pattern": "first", "replacement": "one"},
                    {"pattern": "second", "replacement": "two"},
                    {"pattern": "third", "replacement": "three"},
                ])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "remote_error")
        self.assertEqual(error["failed_index"], 1)
        self.assertEqual(error["applied_indexes"], [0])
        self.assertIn("document_state", error)
        self.assertEqual(run_cli.call_count, 2)

    def test_batch_push_raises_on_lark_failed_write(self) -> None:
        failed = {
            "ok": True,
            "data": {"document": {"revision_id": 2}, "result": "failed",
             "warnings": ["degrade_code=5000000,msg=Document operation failed"]},
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value=failed):
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_push([{"doc": "doc-a", "content": "# t"}])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["failed_index"], 0)
        self.assertEqual(error["cause"]["cause"]["error"], "lark_write_failed")
        self.assertIn("degrade_code=5000000", error["cause"]["cause"]["message"])

    def test_assert_write_succeeded_is_tolerant_of_minimal_envelopes(self) -> None:
        for envelope in ({}, {"ok": True}, {"data": {"document": {"revision_id": 1}}}):
            SERVER._assert_write_succeeded(envelope, "unit-test")

    def test_batch_push_rejects_duplicate_documents(self) -> None:
        with patch.object(SERVER, "_check_lark_cli") as check, \
             patch.object(SERVER, "_run_cli") as run_cli:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_push([
                    {"doc": "doc-a", "content": "one"},
                    {"doc": "doc-a", "content": "two"},
                ])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["failed_index"], 1)
        self.assertIn("more than once", error["cause"]["message"])
        check.assert_not_called()
        run_cli.assert_not_called()

    def test_batch_point_update_stops_on_revision_conflict(self) -> None:
        document = {"revision_id": 7, "content": "first second third"}
        conflict = SERVER.LarkCLIError({
            "error": "lark_cli_failed", "message": "1770021 too old document",
        })
        writes = [{"data": {"document": {"revision_id": 8}}}, conflict]
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_write_exact_text", side_effect=writes) as write:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_point_update("doc-a", [
                    {"pattern": "first", "replacement": "one"},
                    {"pattern": "second", "replacement": "two"},
                    {"pattern": "third", "replacement": "three"},
                ])
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "revision_conflict")
        self.assertEqual(error["failed_index"], 1)
        self.assertEqual(error["completed"], 1)
        self.assertEqual(error["expected_revision_id"], 8)
        self.assertEqual(error["applied_indexes"], [0])
        self.assertEqual(write.call_count, 2)

    def test_batch_point_update_rejects_stale_expected_revision(self) -> None:
        document = {"revision_id": 8, "content": "first"}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_fetch_document", return_value=document), \
             patch.object(SERVER, "_write_exact_text") as write:
            with self.assertRaises(RuntimeError) as raised:
                SERVER.batch_point_update(
                    "doc-a", [{"pattern": "first", "replacement": "one"}],
                    expected_revision_id=7,
                )
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "revision_conflict")
        self.assertEqual(error["actual_revision_id"], 8)
        write.assert_not_called()

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

    def test_lark_cli_verification_is_cached_for_fifteen_minutes(self) -> None:
        auth = {
            "identity": "user", "verified": True,
            "identities": {"user": {"status": "ready", "available": True, "verified": True}},
        }
        completed = subprocess.CompletedProcess([], 0, "lark-cli version 1.0.69", "")
        with patch.object(SERVER, "_auth_cache", None), \
             patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", return_value=auth) as run_cli:
            SERVER._check_lark_cli()
            SERVER._check_lark_cli()
        self.assertEqual(SERVER.AUTH_CACHE_SECONDS, 15 * 60)
        run_cli.assert_called_once_with(
            ["auth", "status", "--json", "--verify"],
            "check lark-cli user authentication",
        )

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

    def test_lark_cli_maps_missing_app_to_setup_tool(self) -> None:
        missing = SERVER.LarkCLIError({
            "message": json.dumps({"error": {"subtype": "not_configured"}}),
        })
        completed = subprocess.CompletedProcess([], 0, "lark-cli version 1.0.56", "")
        with patch.object(SERVER.shutil, "which", return_value="/bin/lark-cli"), \
             patch.object(SERVER.subprocess, "run", return_value=completed), \
             patch.object(SERVER, "_run_cli", side_effect=missing):
            with self.assertRaises(RuntimeError) as raised:
                SERVER._check_lark_cli(use_cache=False)
        error = json.loads(str(raised.exception))
        self.assertEqual(error["error"], "app_not_configured")
        self.assertIn("begin_lark_app_setup", error["next_step"])

    def test_app_setup_url_parser_preserves_the_returned_url(self) -> None:
        stream = SimpleNamespace(fileno=lambda: 7)
        process = SimpleNamespace(stdout=stream, poll=lambda: None)
        with patch.object(SERVER.select, "select", return_value=([stream], [], [])), \
             patch.object(SERVER.os, "read", return_value=b"Open https://example.feishu.cn/setup?x=a%2Fb\n"):
            result = SERVER._read_lark_app_setup_url(process)
        self.assertEqual(result, "https://example.feishu.cn/setup?x=a%2Fb")

    def test_begin_lark_app_setup_requires_confirmation_and_starts_cli(self) -> None:
        process = SimpleNamespace(poll=lambda: None)
        with self.assertRaisesRegex(ValueError, "confirmation"):
            SERVER.begin_lark_app_setup("create")
        with patch.object(SERVER, "_app_setup", None), \
             patch.object(SERVER, "_lark_app_configured", return_value=False), \
             patch.object(SERVER, "_start_lark_app_setup", return_value=process) as start, \
             patch.object(SERVER, "_read_lark_app_setup_url", return_value="https://auth.example/setup"), \
             patch.object(SERVER, "_lark_auth_qrcode", return_value="cG5n"):
            result = SERVER.begin_lark_app_setup(SERVER.APP_SETUP_CONFIRMATION)
        start.assert_called_once_with("feishu", "zh_cn")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["verification_url"], "https://auth.example/setup")

    def test_complete_lark_app_setup_verifies_saved_binding(self) -> None:
        process = SimpleNamespace(
            communicate=Mock(return_value=(b"", None)), returncode=0, poll=lambda: 0,
        )
        with patch.object(SERVER, "_app_setup", (process, "https://auth.example/setup")), \
             patch.object(SERVER, "_auth_cache", (1.0, {"verified": True})), \
             patch.object(SERVER, "_lark_app_configured", return_value=True):
            result = SERVER.complete_lark_app_setup()
            self.assertIsNone(SERVER._auth_cache)
        self.assertEqual(result["status"], "configured")
        process.communicate.assert_called_once_with(
            timeout=SERVER.APP_SETUP_COMPLETE_TIMEOUT_SECONDS,
        )

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
            self.assertEqual(list(SERVER.WORKDIR.glob(".run-*")), [])

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
        # The write may already have applied, so never advise a blind retry.
        self.assertIn("verify with find_document_text", error["next_step"])
        self.assertNotIn("retry once", error["next_step"])

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
            self.assertEqual(list(SERVER.WORKDIR.glob(".run-*")), [])

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

    def test_scan_document_assets_returns_bounded_metadata(self) -> None:
        response = {"data": {"document": {
            "revision_id": "9",
            "content": '<doc><img src="https://example/image.png" token="image-token"/>'
                       '<whiteboard type="mermaid" block-token="board-token">A --&gt; B</whiteboard>'
                       '<p>private body</p></doc>',
        }}}
        with patch.object(SERVER, "_check_lark_cli"), \
             patch.object(SERVER, "_run_cli", return_value=response):
            result = SERVER.scan_document_assets("doc-a")
        self.assertEqual(result["counts"], {"images": 1, "whiteboards": 1})
        self.assertEqual(result["images"][0]["source"], "https://example/image.png")
        self.assertEqual(result["whiteboards"][0]["token"], "board-token")
        self.assertNotIn("private body", json.dumps(result))

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
            self.assertEqual(list(SERVER.WORKDIR.glob(".run-*")), [])

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
            aged = time.time() - SERVER.CLI_TIMEOUT_SECONDS * 3
            os.utime(stale, (aged, aged))
            SERVER._cleanup_stale_runs()
            self.assertFalse(SERVER.WORKDIR.exists())

    def test_server_start_keeps_another_instances_live_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"):
            live = SERVER.WORKDIR / ".run-live"
            live.mkdir(parents=True)
            (live / ".content").write_text("in flight")
            SERVER._cleanup_stale_runs()
            self.assertTrue(live.is_dir())

    def test_hidden_run_survives_concurrent_use(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"):
            failures: list[str] = []

            def churn() -> None:
                for _ in range(200):
                    try:
                        with SERVER._hidden_run() as run:
                            SERVER._payload(run / ".content", "body")
                    except Exception as error:  # noqa: BLE001 - report any failure
                        failures.append(f"{type(error).__name__}: {error}")
                        return

            threads = [threading.Thread(target=churn) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(failures, [])
            self.assertEqual(list(SERVER.WORKDIR.glob(".run-*")), [])

    def test_successful_operation_survives_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
             patch.object(SERVER, "WORKDIR", Path(tmp) / ".lark_publish"), \
             patch.object(SERVER.shutil, "rmtree", side_effect=OSError("busy")):
            with SERVER._hidden_run() as run:
                SERVER._payload(run / ".content", "body")

    def test_public_http_requires_authentication_and_tls(self) -> None:
        with patch.object(SERVER, "AUTH_PROVIDER", None):
            with self.assertRaisesRegex(RuntimeError, "requires configured authentication"):
                SERVER._https_config("0.0.0.0", None, None)

    def test_loopback_http_refuses_to_serve_unauthenticated_by_default(self) -> None:
        with patch.object(SERVER, "AUTH_PROVIDER", None):
            with self.assertRaisesRegex(RuntimeError, "--allow-unauthenticated"):
                SERVER._https_config("127.0.0.1", None, None)
            self.assertEqual(
                SERVER._https_config("127.0.0.1", None, None, True), {},
            )

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
            SERVER.GITHUB_USERS_ENV: "Charles, AnotherUser",
        }
        with patch.dict(SERVER.os.environ, env, clear=True), \
             patch.object(SERVER, "_OriginCompatibleGitHubProvider", return_value="provider") as provider:
            self.assertEqual(SERVER._auth_provider("github"), "provider")
            kwargs = provider.call_args.kwargs
            self.assertEqual(kwargs["base_url"], "https://mcp.example.com")
            self.assertIn("https://chatgpt.com/connector/oauth/*", kwargs["allowed_client_redirect_uris"])
            self.assertIn("https://claude.ai/api/mcp/auth_callback", kwargs["allowed_client_redirect_uris"])
            self.assertIn(SERVER.GROK_REDIRECT_URI, kwargs["allowed_client_redirect_uris"])
            self.assertIn(SERVER.WORKBUDDY_REDIRECT_URI, kwargs["allowed_client_redirect_uris"])
            self.assertIn("http://localhost:*", kwargs["allowed_client_redirect_uris"])
            self.assertIn("http://127.0.0.1:*", kwargs["allowed_client_redirect_uris"])
            self.assertEqual(kwargs["allowed_client_redirect_uris"], SERVER.ALLOWED_CLIENT_REDIRECT_URIS)
            allowed = SERVER._authorized_github_user(SimpleNamespace(token=SimpleNamespace(
                claims={"login": "charles"},
            )))
            self.assertTrue(allowed)
            allowed = SERVER._authorized_github_user(SimpleNamespace(token=SimpleNamespace(
                claims={"login": "anotheruser"},
            )))
            self.assertTrue(allowed)

    def test_github_oauth_rejects_non_https_origin(self) -> None:
        env = {
            SERVER.BASE_URL_ENV: "http://mcp.example.com/path",
            SERVER.GITHUB_CLIENT_ID_ENV: "client",
            SERVER.GITHUB_CLIENT_SECRET_ENV: "secret",
            SERVER.GITHUB_JWT_SIGNING_KEY_ENV: "stable-signing-key",
            SERVER.GITHUB_USERS_ENV: "charles",
        }
        with patch.dict(SERVER.os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HTTPS origin"):
                SERVER._auth_provider("github")

    def test_github_user_list_supports_legacy_setting_without_ambiguity(self) -> None:
        with patch.dict(SERVER.os.environ, {SERVER.GITHUB_USER_ENV: "Charles"}, clear=True):
            self.assertEqual(SERVER._github_users(), frozenset({"charles"}))
        with patch.dict(SERVER.os.environ, {
            SERVER.GITHUB_USERS_ENV: "Charles, AnotherUser",
            SERVER.GITHUB_USER_ENV: "LegacyUser",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "set only one"):
                SERVER._github_users()

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
