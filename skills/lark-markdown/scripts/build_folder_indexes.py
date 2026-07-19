#!/usr/bin/env python3
"""Build Markdown index pages for the Docx nodes representing local folders.

Each folder gets one index page. A folder page lists:
- direct child documents (not recursive), linked by document name;
- direct child folders, linked to their own folder-page node.

The page does not repeat the folder title: the Wiki node already carries it,
and a leading ``## {title}`` would duplicate the document title in Lark.
"""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help="local folder, relative to cwd")
    p.add_argument("--label", required=True, help="root key used in nodes.json")
    p.add_argument("--nodes", required=True)
    p.add_argument("--docs", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    root = Path(args.root)
    nodes = json.loads(Path(args.nodes).read_text())
    docs = json.loads(Path(args.docs).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    folders = [root, *(p for p in root.rglob("*") if p.is_dir() and p.name != "_assets")]
    for folder in sorted(folders, key=lambda p: (len(p.relative_to(root).parts), p.as_posix())):
        relative_folder = folder.relative_to(root).as_posix()
        node_key = args.label if relative_folder == "." else f"{args.label}/{relative_folder}"
        if node_key not in nodes:
            raise SystemExit(f"folder node missing: {node_key}")

        # Direct child documents only (non-recursive), sorted by name.
        direct_docs = sorted(folder.glob("*.md"))
        # Direct child folders that have a node, sorted by name.
        direct_subdirs = sorted(
            child for child in folder.iterdir()
            if child.is_dir() and child.name != "_assets"
            and f"{node_key}/{child.name}" in nodes
        )

        lines = []
        if direct_subdirs:
            lines.append("## 子主题\n")
            for child in direct_subdirs:
                child_key = f"{node_key}/{child.name}"
                url = nodes[child_key] if isinstance(nodes[child_key], str) else nodes[child_key].get("url", "")
                lines.append(f"- [{child.name}]({url})")
            lines.append("")

        if direct_docs:
            lines.append("## 文档链接\n")
            for source in direct_docs:
                rel = source.relative_to(root).as_posix()
                if rel not in docs:
                    raise SystemExit(f"document missing: {rel}")
                entry = docs[rel]
                url = entry["url"] if isinstance(entry, dict) else entry
                name = source.stem
                if isinstance(entry, dict) and entry.get("summary"):
                    lines.append(f"- [{name}]({url})：{entry['summary']}")
                else:
                    lines.append(f"- [{name}]({url})")
            lines.append("")

        if not lines:
            lines.append("（此文件夹暂无 Markdown 文档。）\n")

        target = out / ("index.md" if relative_folder == "." else f"{relative_folder}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip() + "\n")


if __name__ == "__main__":
    main()
