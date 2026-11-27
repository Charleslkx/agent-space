"""Unit tests for lark-doc large media upload and URL download paths."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from starlette.testclient import TestClient

os.environ.setdefault("LARK_MCP_AUTH_MODE", "none")
os.environ.setdefault("LARK_MCP_BASE_URL", "https://lark-doc.example.com")

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mcp_server  # noqa: E402


class MediaUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_base_url = os.environ.get(mcp_server.BASE_URL_ENV)
        os.environ[mcp_server.BASE_URL_ENV] = "https://lark-doc.example.com"
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_workdir = mcp_server.WORKDIR
        self._orig_project_root = mcp_server.PROJECT_ROOT
        mcp_server.PROJECT_ROOT = Path(self._tmp.name).resolve()
        mcp_server.WORKDIR = mcp_server.PROJECT_ROOT / ".lark_publish"
        with mcp_server._media_uploads_lock:
            mcp_server._media_uploads.clear()
            mcp_server._media_tokens.clear()

    def tearDown(self) -> None:
        with mcp_server._media_uploads_lock:
            mcp_server._media_uploads.clear()
            mcp_server._media_tokens.clear()
        mcp_server.WORKDIR = self._orig_workdir
        mcp_server.PROJECT_ROOT = self._orig_project_root
        self._tmp.cleanup()
        if self._orig_base_url is None:
            os.environ.pop(mcp_server.BASE_URL_ENV, None)
        else:
            os.environ[mcp_server.BASE_URL_ENV] = self._orig_base_url

    def _http_client(self) -> TestClient:
        return TestClient(mcp_server.mcp.http_app())

    def test_one_time_upload_and_insert_media_end_to_end(self) -> None:
        res = mcp_server.create_media_upload([
            mcp_server.MediaUploadSpec(filename="fig01.png", media_type="image"),
        ])
        self.assertEqual(len(res["uploads"]), 1)
        item = res["uploads"][0]
        self.assertTrue(item["upload_url"].startswith("https://lark-doc.example.com/media/upload/"))
        token = item["upload_url"].rsplit("/", 1)[-1]
        payload = b"\x89PNG\r\n\x1a\n" + b"A" * 4096

        with self._http_client() as client:
            put_resp = client.put(f"/media/upload/{token}", content=payload)
            self.assertEqual(put_resp.status_code, 200)
            body = put_resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["upload_id"], item["upload_id"])
            self.assertEqual(body["size_bytes"], len(payload))
            self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())

            # Second PUT with the same one-time token must fail.
            dup_resp = client.put(f"/media/upload/{token}", content=payload)
            self.assertEqual(dup_resp.status_code, 404)

        cli_calls: list[list[str]] = []

        def fake_run_cli(args: list[str], operation: str) -> dict:
            cli_calls.append(args)
            if args[:2] == ["docs", "+media-insert"]:
                file_arg = args[args.index("--file") + 1]
                staged = (mcp_server.PROJECT_ROOT / file_arg).read_bytes()
                self.assertEqual(staged, payload)
                return {"ok": True, "data": {"block_id": "doxcnNewImg"}}
            return {"ok": True, "data": {"result": "success"}}

        with (
            mock.patch.object(mcp_server, "_check_lark_cli"),
            mock.patch.object(mcp_server, "_run_cli", side_effect=fake_run_cli),
            mock.patch.object(mcp_server, "_run_block_command") as mock_move,
        ):
            insert_res = mcp_server.insert_media(
                doc="doc_token_1",
                upload_id=item["upload_id"],
                after_block_id="doxcnCaption1",
                align="center",
            )

        self.assertTrue(insert_res["ok"])
        self.assertEqual(len(cli_calls), 1)
        self.assertIn("--align", cli_calls[0])
        self.assertIn("center", cli_calls[0])
        mock_move.assert_called_once_with(
            "doc_token_1",
            "block_move_after",
            block_id="doxcnCaption1",
            src_block_ids="doxcnNewImg",
            operation="insert_media move",
        )
        # Temporary upload directory must be removed after consumption.
        self.assertEqual(list(mcp_server.WORKDIR.glob(".upload-*")), [])

    def test_upload_rejects_oversized_empty_and_expired_tokens(self) -> None:
        res = mcp_server.create_media_upload([
            mcp_server.MediaUploadSpec(filename="big.png"),
            mcp_server.MediaUploadSpec(filename="empty.png"),
            mcp_server.MediaUploadSpec(filename="expired.png"),
        ])
        tok_big = res["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        tok_empty = res["uploads"][1]["upload_url"].rsplit("/", 1)[-1]
        tok_exp = res["uploads"][2]["upload_url"].rsplit("/", 1)[-1]
        uid_exp = res["uploads"][2]["upload_id"]

        with mcp_server._media_uploads_lock:
            mcp_server._media_uploads[uid_exp].expires_at = time.time() - 10

        with self._http_client() as client:
            with mock.patch.object(mcp_server, "MAX_MEDIA_BYTES", 16):
                r_big = client.put(f"/media/upload/{tok_big}", content=b"x" * 17)
                self.assertEqual(r_big.status_code, 413)

            r_empty = client.put(f"/media/upload/{tok_empty}", content=b"")
            self.assertEqual(r_empty.status_code, 400)

            r_exp = client.put(f"/media/upload/{tok_exp}", content=b"ok")
            self.assertEqual(r_exp.status_code, 404)

        self.assertEqual(list(mcp_server.WORKDIR.glob(".upload-*")), [])

    def test_insert_media_source_validation_and_legacy_base64(self) -> None:
        with mock.patch.object(mcp_server, "_check_lark_cli"):
            with self.assertRaises(ValueError):
                mcp_server.insert_media(doc="doc1", filename="a.png")
            with self.assertRaises(ValueError):
                mcp_server.insert_media(
                    doc="doc1",
                    filename="a.png",
                    content_base64=base64.b64encode(b"hi").decode(),
                    source_url="https://example.com/a.png",
                )

        # Un-uploaded ticket should be rejected.
        up = mcp_server.create_media_upload([mcp_server.MediaUploadSpec(filename="pending.png")])
        with mock.patch.object(mcp_server, "_check_lark_cli"):
            with self.assertRaisesRegex(ValueError, "not been uploaded"):
                mcp_server.insert_media(doc="doc1", upload_id=up["uploads"][0]["upload_id"])

        # Legacy base64 path still works.
        raw = b"tiny-png-bytes"
        b64 = base64.b64encode(raw).decode()
        with (
            mock.patch.object(mcp_server, "_check_lark_cli"),
            mock.patch.object(mcp_server, "_run_cli", return_value={"ok": True, "data": {"block_id": "b1"}}),
        ):
            res = mcp_server.insert_media(doc="doc1", filename="tiny.png", content_base64=b64)
            self.assertTrue(res["ok"])

    def test_source_url_ssrf_protection_and_download(self) -> None:
        for bad_url in [
            "http://example.com/a.png",
            "file:///etc/passwd",
            "https://user:pass@example.com/a.png",
        ]:
            with self.assertRaises(ValueError):
                mcp_server._validate_public_https_url(bad_url)

        for private_ip in ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1"]:
            fake_addr = [(socket.AF_INET6 if ":" in private_ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (private_ip, 443))]
            with mock.patch.object(socket, "getaddrinfo", return_value=fake_addr):
                with self.assertRaisesRegex(ValueError, "non-public IP"):
                    mcp_server._validate_public_https_url("https://internal.example.com/img.png")

        # Redirect to private IP must be blocked.
        def fake_getaddrinfo(host, port, type=0):
            if host == "public.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

        redirect_transport = httpx.MockTransport(
            lambda req: httpx.Response(302, headers={"Location": "https://metadata.example.com/latest"})
        )
        orig_client = httpx.Client

        def patched_client(*args, **kwargs):
            kwargs["transport"] = redirect_transport
            return orig_client(*args, **kwargs)

        with (
            mock.patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch.object(httpx, "Client", side_effect=patched_client),
        ):
            with self.assertRaisesRegex(ValueError, "non-public IP"):
                mcp_server._download_media_from_url(
                    "https://public.example.com/start.png",
                    Path(self._tmp.name) / "out.png",
                )

        # Valid public HTTPS download succeeds.
        ok_transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"PNGDATA"))

        def patched_ok_client(*args, **kwargs):
            kwargs["transport"] = ok_transport
            return orig_client(*args, **kwargs)

        out_path = Path(self._tmp.name) / "ok.png"
        with (
            mock.patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch.object(httpx, "Client", side_effect=patched_ok_client),
        ):
            mcp_server._download_media_from_url("https://public.example.com/ok.png", out_path)
        self.assertEqual(out_path.read_bytes(), b"PNGDATA")

        # HTTP 404 from remote host is wrapped into a clean ValueError.
        err_transport = httpx.MockTransport(lambda req: httpx.Response(404))

        def patched_err_client(*args, **kwargs):
            kwargs["transport"] = err_transport
            return orig_client(*args, **kwargs)

        with (
            mock.patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch.object(httpx, "Client", side_effect=patched_err_client),
        ):
            with self.assertRaisesRegex(ValueError, "failed to download source_url"):
                mcp_server._download_media_from_url("https://public.example.com/missing.png", out_path)

    def test_in_flight_uploading_ticket_not_purged_and_bad_filename_preserves_upload(self) -> None:
        res = mcp_server.create_media_upload([mcp_server.MediaUploadSpec(filename="keep.png")])
        uid = res["uploads"][0]["upload_id"]
        tok = res["uploads"][0]["upload_url"].rsplit("/", 1)[-1]

        # Claiming a token marks it uploading and protects it from expiry purge mid-stream.
        with mcp_server._media_uploads_lock:
            claimed = mcp_server._claim_upload_token_locked(tok)
            self.assertIsNotNone(claimed)
            claimed.expires_at = time.time() - 5
            mcp_server._purge_expired_uploads_locked()
            self.assertIn(uid, mcp_server._media_uploads)

        # Complete upload normally on a fresh ticket, then pass invalid filename override.
        res2 = mcp_server.create_media_upload([mcp_server.MediaUploadSpec(filename="safe.png")])
        uid2 = res2["uploads"][0]["upload_id"]
        tok2 = res2["uploads"][0]["upload_url"].rsplit("/", 1)[-1]
        with self._http_client() as client:
            self.assertEqual(client.put(f"/media/upload/{tok2}", content=b"DATA").status_code, 200)

        with mock.patch.object(mcp_server, "_check_lark_cli"):
            with self.assertRaisesRegex(ValueError, "plain file name"):
                mcp_server.insert_media(doc="doc1", filename="../escape.png", upload_id=uid2)
            # Ticket and staged file remain intact so caller can retry with a valid filename.
            self.assertIn(uid2, mcp_server._media_uploads)


if __name__ == "__main__":
    unittest.main()
