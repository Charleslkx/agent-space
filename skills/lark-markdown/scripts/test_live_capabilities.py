#!/usr/bin/env python3
"""Live Markdown and Lark-native block checks through the HTTP MCP endpoint."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re

from fastmcp import Client


MARKDOWN = """# Markdown capability test

## Inline

plain **bold** *italic* ~~strike~~ `inline-code` $a+b$ [link](https://example.com)

## Lists

- unordered
  - nested

1. ordered
2. second

> blockquote-marker

```python
print("code-fence-marker")
```

| name | value |
|---|---:|
| table-marker | 42 |

---

hard break  
next line
"""

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


def document_token(doc: str) -> str:
    return doc.rstrip("/").rsplit("/", 1)[-1]


async def run(url: str, doc: str) -> None:
    async with Client(url) as client:
        tools = {tool.name for tool in await client.list_tools()}
        expected = {
            "check_lark_cli", "begin_lark_auth", "complete_lark_auth",
            "batch_pull", "batch_push", "point_update",
            "create_document", "insert_media", "whiteboard_query", "whiteboard_update",
        }
        assert tools == expected, tools

        await client.call_tool("batch_push", {"documents": [{
            "doc": doc,
            "content": MARKDOWN,
            "mode": "overwrite",
            "doc_format": "markdown",
        }]})
        markdown = (await client.call_tool("batch_pull", {
            "documents": [doc], "doc_format": "markdown",
        })).data[0]["content"]
        markdown_markers = {
            "heading": "Markdown capability test",
            "bold": "bold",
            "italic": "italic",
            "strike": "strike",
            "inline_code": "inline-code",
            "inline_math": "a+b",
            "link": "https://example.com",
            "nested_list": "nested",
            "blockquote": "blockquote-marker",
            "code_block": "code-fence-marker",
            "table": "table-marker",
            "hard_break": "next line",
        }
        missing = [name for name, marker in markdown_markers.items() if marker not in markdown]
        assert not missing, missing
        await client.call_tool("point_update", {
            "doc": doc, "pattern": "plain ", "replacement": "plain-updated ",
        })
        updated = (await client.call_tool("batch_pull", {
            "documents": [doc], "doc_format": "markdown",
        })).data[0]["content"]
        assert "plain-updated" in updated
        await client.call_tool("insert_media", {
            "doc": doc,
            "filename": "mcp-media-test.png",
            "content_base64": base64.b64encode(PNG).decode(),
            "selection": "plain-updated",
            "before": True,
        })

        token = document_token(doc)
        native_xml = f"""
<h2>Feishu native blocks</h2>
<p><u>underline-marker</u> <span text-color="green" background-color="light-green">color-marker</span></p>
<checkbox done="true">checked-marker</checkbox>
<checkbox done="false">unchecked-marker</checkbox>
<callout emoji="💡" background-color="light-blue" border-color="blue"><p>callout-marker</p></callout>
<grid><column width-ratio="0.5"><p>left-column-marker</p></column><column width-ratio="0.5"><p>right-column-marker</p></column></grid>
<p><cite type="doc" doc-id="{token}"></cite></p>
<bookmark name="bookmark-marker" href="https://example.com"></bookmark>
<p><a type="url-preview" href="https://example.com">preview-marker</a></p>
<button action="OpenLink" src="https://example.com">button-marker</button>
<time expire-time="1893456000000" notify-time="1893452400000" should-notify="false">reminder-marker</time>
<whiteboard type="mermaid">flowchart LR
A[MD] --&gt; B[Feishu]</whiteboard>
"""
        native_push = (await client.call_tool("batch_push", {"documents": [{
            "doc": doc,
            "content": native_xml,
            "mode": "append",
            "doc_format": "xml",
        }]})).data[0]["result"]
        blocks = native_push.get("data", {}).get("document", {}).get("new_blocks", [])
        board = next((block.get("block_token") for block in blocks
                      if block.get("block_type") == "whiteboard"), None)
        assert board, blocks

        xml = (await client.call_tool("batch_pull", {
            "documents": [doc], "doc_format": "xml", "detail": "full",
        })).data[0]["content"]
        markdown_tags = [
            "<h2", "<b>", "<em>", "<del>", "<code>", "<latex>",
            "<ul", "<ol", "<blockquote", "<pre", "<table", "<hr", "<a ",
        ]
        missing_markdown_tags = [tag for tag in markdown_tags if tag not in xml]
        assert not missing_markdown_tags, missing_markdown_tags
        assert "<title" in xml or "<h1" in xml
        native_tags = [
            "<u>", "<span ", "<checkbox", "<callout", "<grid", "<cite",
            "<bookmark", "<whiteboard",
        ]
        missing_tags = [tag for tag in native_tags if tag not in xml]
        assert not missing_tags, missing_tags
        assert xml.index("<img") < xml.index("plain-updated")
        downgrade_markers = {
            "url_preview": "preview-marker",
            "button": "button-marker",
            "reminder": "reminder-marker",
        }
        downgrades = {
            name: "text_only" if marker in xml else "dropped"
            for name, marker in downgrade_markers.items()
        }

        before = (await client.call_tool("whiteboard_query", {
            "whiteboard_token": board, "output_as": "code",
        })).data
        await client.call_tool("whiteboard_update", {
            "whiteboard_token": board,
            "source": "flowchart LR\nA[HTTP MCP] --> B[lark-cli] --> C[Feishu]",
            "input_format": "mermaid",
            "overwrite": True,
        })
        after = (await client.call_tool("whiteboard_query", {
            "whiteboard_token": board, "output_as": "code",
        })).data
        assert "HTTP MCP" in json.dumps(after, ensure_ascii=False), after

        print(json.dumps({
            "tools": sorted(tools),
            "markdown_checks": len(markdown_markers),
            "markdown_title": "title_or_h1",
            "markdown_xml_tags": markdown_tags,
            "native_tags": native_tags,
            "native_downgrades": downgrades,
            "whiteboard_token": board,
            "whiteboard_query_before": bool(re.search("MD", json.dumps(before))),
            "whiteboard_update_verified": True,
            "point_update_verified": True,
            "media_position_verified": True,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--doc", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.doc))
