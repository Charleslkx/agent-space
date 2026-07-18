#!/usr/bin/env python3
"""Remove disposable publish artifacts while preserving recovery state."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

KEEP = {"state.json", "url-map.json", "report.json"}


def cleanup(workdir: Path) -> list[str]:
    if workdir.name != ".lark_publish":
        raise ValueError("refusing to clean a directory not named .lark_publish")
    if workdir.is_symlink():
        raise ValueError("refusing to clean a symlink")
    if not workdir.exists():
        return []
    removed = []
    for path in workdir.iterdir():
        if path.name in KEEP:
            continue
        removed.append(path.name)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    if not any(workdir.iterdir()):
        workdir.rmdir()
    return sorted(removed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, default=Path(".lark_publish"))
    args = parser.parse_args()
    print("removed=" + ",".join(cleanup(args.workdir)))


if __name__ == "__main__":
    main()
