#!/usr/bin/env python3
"""Live HTTP integration check against two disposable Lark documents."""
from __future__ import annotations

import argparse
import asyncio
import json

from fastmcp import Client


async def run(url: str, documents: list[str]) -> None:
    async with Client(url) as client:
        tools = {tool.name for tool in await client.list_tools()}
        expected = {
            "check_lark_cli", "batch_pull", "batch_push", "point_update",
            "whiteboard_query", "whiteboard_update",
        }
        assert tools == expected, tools
        status = (await client.call_tool("check_lark_cli", {})).data
        push = (await client.call_tool("batch_push", {"documents": [
            {
                "doc": documents[0],
                "content": "# MCP HTTP Test A\n\nalpha-v1",
                "mode": "overwrite",
                "doc_format": "markdown",
            },
            {
                "doc": documents[1],
                "content": "\n\nbeta-appended",
                "mode": "append",
                "doc_format": "markdown",
            },
        ]})).data
        pulled = (await client.call_tool("batch_pull", {"documents": documents})).data
        assert "alpha-v1" in pulled[0]["content"]
        assert "beta-appended" in pulled[1]["content"]
        updated = (await client.call_tool("point_update", {
            "doc": documents[0],
            "pattern": "alpha-v1",
            "replacement": "alpha-v2",
            "doc_format": "markdown",
        })).data
        verified = (await client.call_tool("batch_pull", {"documents": [documents[0]]})).data
        assert "alpha-v2" in verified[0]["content"]
        assert "alpha-v1" not in verified[0]["content"]
        print(json.dumps({
            "tools": sorted(tools),
            "status": status,
            "push_count": len(push),
            "pull_count": len(pulled),
            "point_update_result": updated.get("data", {}).get("result"),
            "verified": True,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--doc", action="append", required=True)
    args = parser.parse_args()
    if len(args.doc) != 2:
        parser.error("pass exactly two --doc values")
    asyncio.run(run(args.url, args.doc))
