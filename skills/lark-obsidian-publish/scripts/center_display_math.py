#!/usr/bin/env python3
"""Convert Markdown $$...$$ blocks to centered Lark DocxXML paragraphs."""
from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path


FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")


def convert_text(content: str) -> tuple[str, int]:
    """Convert standalone display formulas, excluding fenced code blocks."""
    count = 0

    def convert(chunk: str) -> str:
        nonlocal count

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            formula = html.escape(match.group(1).strip(), quote=False)
            return f'<p align="center"><latex>{formula}</latex></p>'

        return re.sub(
            r"(?ms)^[ \t]*\$\$[ \t]*(?:\n)?(.*?)(?:\n)?[ \t]*\$\$[ \t]*$",
            replace,
            chunk,
        )

    output: list[str] = []
    prose: list[str] = []
    closing: re.Pattern[str] | None = None
    for line in content.splitlines(keepends=True):
        if closing:
            output.append(line)
            if closing.match(line):
                closing = None
            continue
        match = FENCE.match(line)
        if match:
            output.append(convert("".join(prose)))
            prose.clear()
            marker = match.group(1)
            closing = re.compile(rf"^[ ]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
            output.append(line)
        else:
            prose.append(line)
    output.append(convert("".join(prose)))
    return "".join(output), count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not args.source.is_dir():
        parser.error(f"not a directory: {args.source}")
    shutil.rmtree(args.destination, ignore_errors=True)
    counts: dict[str, int] = {}
    for path in args.source.rglob("*.md"):
        content, count = convert_text(path.read_text())
        output = args.destination / path.relative_to(args.source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        if count:
            counts[str(path.relative_to(args.source))] = count
    print(f"centered_display_formulas={sum(counts.values())}")


if __name__ == "__main__":
    main()
