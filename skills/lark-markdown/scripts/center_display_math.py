#!/usr/bin/env python3
"""Preprocess Markdown for Lark Docx: center display formulas and harden bold.

Three transforms, all skipping fenced code blocks:
- ``$$...$$`` display formulas -> centered ``<p align="center"><latex>...</latex></p>``.
- ``\\(...\\)`` LaTeX inline math -> ``$...$``. Lark only renders ``$...$``;
  ``\\(...\\)`` degrades to literal ``(\\alpha)`` text if left in place.
- ``**...**`` bold -> ``<b>...</b>``. Lark's Markdown bold parser mis-bounds
  ``**词**（`` and similar CJK-punctuation patterns; the native ``<b>`` tag
  has no such ambiguity. Only paired ``**...**`` runs are converted.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path


FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
DISPLAY_FORMULA = re.compile(r"(?ms)^[ \t]*\$\$[ \t]*(?:\n)?(.*?)(?:\n)?[ \t]*\$\$[ \t]*$")
LATEX_INLINE = re.compile(r"\\\((.+?)\\\)")
BOLD_PAIR = re.compile(r"\*\*([^*\n]+?)\*\*")


def _convert_inline_latex(text: str) -> str:
    """Rewrite \\(...\\) LaTeX inline math to $...$ so Lark renders it."""
    return LATEX_INLINE.sub(lambda m: f"${m.group(1).strip()}$", text)


def _convert_bold(text: str) -> str:
    """Rewrite paired **...** to <b>...</b> to avoid Lark bold-boundary bugs."""
    return BOLD_PAIR.sub(lambda m: f"<b>{m.group(1)}</b>", text)


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

        chunk = _convert_inline_latex(chunk)
        chunk = _convert_bold(chunk)
        return DISPLAY_FORMULA.sub(replace, chunk)

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
    if args.destination.is_symlink():
        parser.error("destination must not be a symlink")
    source = args.source.resolve()
    destination = args.destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        parser.error("source and destination directories must not overlap")
    shutil.rmtree(destination, ignore_errors=True)
    count_total = 0
    for path in source.rglob("*.md"):
        content, count = convert_text(path.read_text())
        output = destination / path.relative_to(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        count_total += count
    print(f"centered_display_formulas={count_total}")


if __name__ == "__main__":
    main()
