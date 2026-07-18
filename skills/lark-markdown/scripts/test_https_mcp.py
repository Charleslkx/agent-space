#!/usr/bin/env python3
"""Verify TLS and Bearer authentication against a running MCP server."""
from __future__ import annotations

import argparse
import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def run(url: str, token: str, ca_cert: str | bool) -> None:
    try:
        async with Client(StreamableHttpTransport(url, verify=ca_cert)) as client:
            await client.list_tools()
    except Exception as error:
        assert "401" in str(error), error
    else:
        raise AssertionError("unauthenticated request unexpectedly succeeded")

    transport = StreamableHttpTransport(url, auth=token, verify=ca_cert)
    async with Client(transport) as client:
        tools = await client.list_tools()
    assert tools
    print(f"https_auth_verified=true tools={len(tools)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-cert")
    parser.add_argument("--token-env", default="LARK_MCP_AUTH_TOKEN")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        parser.error(f"missing environment variable: {args.token_env}")
    asyncio.run(run(args.url, token, args.ca_cert or True))
