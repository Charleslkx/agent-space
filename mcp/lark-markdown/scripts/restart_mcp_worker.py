#!/usr/bin/env python3
"""Delayed restart worker launched only by schedule_mcp_restart."""
from __future__ import annotations

import argparse
import subprocess
import time

SERVICE_NAME = "lark-markdown-mcp.service"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=int, required=True)
    args = parser.parse_args()
    if not 5 <= args.delay <= 300:
        raise SystemExit("delay must be between 5 and 300")
    time.sleep(args.delay)
    subprocess.run(
        ["sudo", "-n", "systemctl", "restart", SERVICE_NAME],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    main()
