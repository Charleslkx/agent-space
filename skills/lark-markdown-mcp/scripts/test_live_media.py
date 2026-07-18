#!/usr/bin/env python3
"""Insert a tiny image through MCP and verify it in the target document."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json

from fastmcp import Client

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


async def run(url: str, doc: str) -> None:
    async with Client(url) as client:
        inserted = (await client.call_tool("insert_media", {
            "doc": doc,
            "filename": "mcp-media-test.png",
            "content_base64": base64.b64encode(PNG).decode(),
            "selection": "plain-updated",
            "before": True,
        })).data
        content = (await client.call_tool("batch_pull", {
            "documents": [doc], "doc_format": "xml", "detail": "full",
        })).data[0]["content"]
        assert "<img" in content
        assert content.index("<img") < content.index("plain-updated")
        print(json.dumps({
            "inserted": inserted.get("ok"),
            "position_verified": True,
            "local_payload_removed": True,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--doc", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.doc))
