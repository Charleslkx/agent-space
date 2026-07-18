#!/usr/bin/env python3
"""Build Markdown index pages for the Docx nodes representing local folders."""
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
        links = []
        for source in sorted(folder.rglob("*.md")):
            rel = source.relative_to(root).as_posix()
            doc_key = f"knowledge-base/{args.label}/{rel}"
            if doc_key not in docs:
                raise SystemExit(f"document missing: {doc_key}")
            links.append(f"- [{rel[:-3]}]({docs[doc_key]['url']})")
        title = root.name if relative_folder == "." else folder.name
        content = f"## {title}\n\n## 文档链接\n\n" + ("\n".join(links) if links else "（此文件夹暂无 Markdown 文档。）") + "\n"
        target = out / ("index.md" if relative_folder == "." else f"{relative_folder}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


if __name__ == "__main__":
    main()
