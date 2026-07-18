#!/usr/bin/env python3
"""Insert a tiny image at a marker and remove the local hidden payload."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)
WORKDIR = Path(".lark_publish")
IMAGE = WORKDIR / ".media-test.png"
ENV = {
    **os.environ,
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}


def cli(*args: str) -> dict:
    result = subprocess.run(
        ["lark-cli", *args], text=True, capture_output=True, env=ENV, check=True
    )
    return json.loads(result.stdout)


def main(doc: str) -> None:
    WORKDIR.mkdir(exist_ok=True)
    IMAGE.write_bytes(PNG)
    try:
        inserted = cli(
            "docs", "+media-insert", "--as", "user", "--doc", doc,
            "--file", IMAGE.as_posix(), "--selection-with-ellipsis", "alpha-v2",
            "--before", "--format", "json",
        )
        fetched = cli(
            "docs", "+fetch", "--api-version", "v2", "--as", "user",
            "--doc", doc, "--detail", "full", "--format", "json",
        )
        content = fetched["data"]["document"]["content"]
        assert "<img" in content
        assert content.index("<img") < content.index("alpha-v2")
        print(json.dumps({
            "inserted": inserted.get("ok"),
            "position_verified": True,
            "local_payload_removed": True,
        }, ensure_ascii=False, indent=2))
    finally:
        IMAGE.unlink(missing_ok=True)
        if WORKDIR.exists() and not any(WORKDIR.iterdir()):
            WORKDIR.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    args = parser.parse_args()
    main(args.doc)
