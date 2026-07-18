#!/usr/bin/env python3
"""Build a Feishu upload manifest and staged Markdown without touching source files."""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

MD_IMAGE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
WIKI_IMAGE = re.compile(r'!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
REFERENCE_LINK = re.compile(
    r"(?m)^[ ]{0,3}\[[^\]\n]+\]:[ \t]*(?:<(?P<angle>[^>\n]+)>|(?P<plain>\S+))"
)

def resolve(base: Path, raw: str) -> Path | None:
    raw = unquote(raw.strip().strip('<>'))
    if '://' in raw or raw.startswith(('mailto:', '#')):
        return None
    return (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()

def split_dest(dest: str) -> tuple[str, str]:
    d = dest.strip()
    return d.split('#', 1) if '#' in d else (d, '')


def prose_chunks(content: str) -> list[tuple[bool, str]]:
    """Split Markdown into fenced-code and prose chunks."""
    chunks: list[tuple[bool, str]] = []
    current: list[str] = []
    in_fence = False
    closing: re.Pattern[str] | None = None
    for line in content.splitlines(keepends=True):
        match = FENCE.match(line)
        if not in_fence and match:
            if current:
                chunks.append((False, "".join(current)))
            current = [line]
            marker = match.group(1)
            closing = re.compile(rf"^[ ]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
            in_fence = True
        elif in_fence:
            current.append(line)
            if closing and closing.match(line.rstrip("\r\n")):
                chunks.append((True, "".join(current)))
                current = []
                in_fence = False
                closing = None
        else:
            current.append(line)
    if current:
        chunks.append((in_fence, "".join(current)))
    return chunks


def balanced_close(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def split_link_inner(inner: str) -> tuple[int, int] | None:
    start = len(inner) - len(inner.lstrip())
    if start == len(inner):
        return None
    if inner[start] == "<":
        end = inner.find(">", start + 1)
        return (start + 1, end) if end >= 0 else None
    escaped = False
    depth = 0
    for index in range(start, len(inner)):
        char = inner[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char.isspace() and depth == 0:
            return start, index
    return start, len(inner)


def markdown_links(prose: str):
    """Yield link target spans while skipping inline code and images."""
    index = 0
    while index < len(prose):
        if prose[index] == "`":
            run = len(prose[index:]) - len(prose[index:].lstrip("`"))
            end = prose.find("`" * run, index + run)
            index = len(prose) if end < 0 else end + run
            continue
        if prose.startswith("[[", index) and (index == 0 or prose[index - 1] != "!"):
            end = prose.find("]]", index + 2)
            if end >= 0:
                body = prose[index + 2:end]
                target, separator, alias = body.partition("|")
                yield {
                    "kind": "wiki", "start": index, "end": end + 2,
                    "target": target, "label": alias if separator else target.split("#", 1)[0],
                }
                index = end + 2
                continue
        if prose[index] == "[" and (index == 0 or prose[index - 1] != "!"):
            label_end = balanced_close(prose, index, "[", "]")
            if label_end is not None and label_end + 1 < len(prose) and prose[label_end + 1] == "(":
                link_end = balanced_close(prose, label_end + 1, "(", ")")
                if link_end is not None:
                    inner_start = label_end + 2
                    span = split_link_inner(prose[inner_start:link_end])
                    if span:
                        start, end = span
                        yield {
                            "kind": "markdown", "start": inner_start + start,
                            "end": inner_start + end,
                            "target": prose[inner_start + start:inner_start + end],
                            "label": prose[index + 1:label_end],
                        }
                    index = link_end + 1
                    continue
        index += 1
    for match in REFERENCE_LINK.finditer(prose):
        group = "angle" if match.group("angle") is not None else "plain"
        yield {
            "kind": "markdown", "start": match.start(group), "end": match.end(group),
            "target": match.group(group), "label": "",
        }


def document_target(base: Path, raw: str, files: list[Path]) -> Path | None:
    target_raw, _ = split_dest(raw)
    if not target_raw:
        return base.resolve()
    target = resolve(base.parent, target_raw)
    if target and target.suffix.lower() != ".md":
        target = target.with_suffix(".md")
    if target in files:
        return target
    if "/" not in target_raw and "\\" not in target_raw:
        matches = [path for path in files if path.stem == Path(target_raw).stem]
        if len(matches) == 1:
            return matches[0]
    return None


def transform_prose(content: str, transform) -> str:
    return "".join(chunk if is_code else transform(chunk) for is_code, chunk in prose_chunks(content))


def reset_staging(out: Path) -> None:
    keep = {"state.json", "url-map.json", "report.json"}
    for path in out.iterdir():
        if path.name in keep:
            continue
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path, help='local Markdown directory')
    ap.add_argument('--out', type=Path, required=True, help='hidden .lark_publish staging directory')
    ap.add_argument('--url-map', type=Path, help='JSON object: source relative path -> Feishu doc URL')
    args = ap.parse_args()
    root = args.source.resolve(); out = args.out.resolve()
    if not root.is_dir(): ap.error(f'not a directory: {root}')
    if out.name != '.lark_publish': ap.error('output directory must be named .lark_publish')
    if root == out or root in out.parents or out in root.parents:
        ap.error('source and output directories must not overlap')
    files = sorted(root.rglob('*.md'))
    if not files: ap.error('no Markdown files')
    url_map = json.loads(args.url_map.read_text()) if args.url_map else {}
    out.mkdir(parents=True, exist_ok=True)
    reset_staging(out)
    source_keys = {p.relative_to(root).as_posix(): p for p in files}
    docs, edges, images, errors = [], [], [], []

    for p in files:
        rel = p.relative_to(root).as_posix(); text = p.read_text(encoding='utf-8')
        bad = CONTROL.search(text)
        if bad:
            errors.append({'file': rel, 'error': f'control character U+{ord(bad.group()):04X} at offset {bad.start()}'})
        refs = []
        for is_code, prose in prose_chunks(text):
            if is_code:
                continue
            for link in markdown_links(prose):
                target_raw, fragment = split_dest(link["target"])
                target = document_target(p, target_raw, files)
                if target:
                    target_rel = target.relative_to(root).as_posix()
                    refs.append(target_rel); edges.append({'from': rel, 'to': target_rel, 'fragment': fragment})
                elif link["kind"] == "wiki" or target_raw.lower().endswith(".md"):
                    errors.append({'file': rel, 'error': f'unresolved document link: {link["target"]}'})
            for pattern, kind in ((MD_IMAGE, 'markdown'), (WIKI_IMAGE, 'wikilink')):
                for m in pattern.finditer(prose):
                    raw = m.group(2 if kind == 'markdown' else 1); target = resolve(p.parent, raw)
                    if target and target.exists() and target.is_file():
                        marker_source = rel + "\0" + str(target)
                        marker = f'LOCAL_IMAGE_{hashlib.sha256(marker_source.encode()).hexdigest()[:16]}'
                        images.append({'document': rel, 'source': str(target), 'syntax': kind,
                                       'alt': m.group(1) if kind == 'markdown' else '', 'marker': marker})
                    elif not re.match(r'https?://', raw.strip()):
                        errors.append({'file': rel, 'error': f'unresolved image: {raw}'})
        docs.append({'path': rel, 'title': p.stem, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'references': sorted(set(refs))})

    title_groups: dict[str, list[str]] = defaultdict(list)
    for document in docs:
        title_groups[document['title']].append(document['path'])
    duplicate_titles = [
        {'title': title, 'paths': paths}
        for title, paths in title_groups.items() if len(paths) > 1
    ]
    report = {'documents': docs, 'edges': edges, 'images': images,
              'duplicate_titles': duplicate_titles, 'errors': errors,
              'url_map_complete': len(url_map) == len(files) and set(url_map) == set(source_keys)}
    (out/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    if not args.url_map:
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 1 if errors else 0

    missing = sorted(set(source_keys)-set(url_map)); extra = sorted(set(url_map)-set(source_keys))
    if missing or extra:
        print(json.dumps({'missing_url_map': missing, 'extra_url_map': extra}, ensure_ascii=False, indent=2), file=sys.stderr); return 2
    image_markers = {(item['document'], Path(item['source']).resolve()): item['marker'] for item in images}
    for p in files:
        rel=p.relative_to(root).as_posix(); text=p.read_text(encoding='utf-8')
        def rewrite_links(prose: str) -> str:
            replacements = []
            for link in markdown_links(prose):
                target = document_target(p, link['target'], files)
                if not target:
                    continue
                url = url_map[target.relative_to(root).as_posix()]
                if link['kind'] == 'wiki':
                    replacements.append((link['start'], link['end'], f"[{link['label']}]({url})"))
                else:
                    replacements.append((link['start'], link['end'], url))
            for start, end, replacement in sorted(replacements, reverse=True):
                prose = prose[:start] + replacement + prose[end:]
            return prose
        staged=transform_prose(text, rewrite_links)
        def replace_image(match: re.Match[str], kind: str) -> str:
            raw = match.group(2 if kind == 'markdown' else 1)
            target = resolve(p.parent, raw)
            marker = image_markers.get((rel, target)) if target else None
            return marker if marker else match.group(0)
        staged = transform_prose(staged, lambda prose: MD_IMAGE.sub(lambda m: replace_image(m, 'markdown'), prose))
        staged = transform_prose(staged, lambda prose: WIKI_IMAGE.sub(lambda m: replace_image(m, 'wikilink'), prose))
        dst=out/'markdown'/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_text(staged,encoding='utf-8')
    print(json.dumps({'staged': len(files), 'images': len(images), 'errors': errors}, ensure_ascii=False))
    return 1 if errors else 0
if __name__ == '__main__': raise SystemExit(main())
