#!/usr/bin/env python3
"""Build a Feishu upload manifest and staged Markdown without touching source files."""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

MD_LINK = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)')
MD_IMAGE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
WIKI_IMAGE = re.compile(r'!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

def resolve(base: Path, raw: str) -> Path | None:
    raw = unquote(raw.strip().strip('<>'))
    if '://' in raw or raw.startswith(('mailto:', '#')):
        return None
    return (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()

def split_dest(dest: str) -> tuple[str, str]:
    d = dest.strip()
    return d.split('#', 1) if '#' in d else (d, '')

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path, help='local Markdown directory')
    ap.add_argument('--out', type=Path, required=True, help='empty staging directory')
    ap.add_argument('--url-map', type=Path, help='JSON object: source relative path -> Feishu doc URL')
    ap.add_argument('--allow-control-chars', action='store_true', help='stage them unchanged; never upload by default')
    args = ap.parse_args()
    root = args.source.resolve(); out = args.out.resolve()
    if not root.is_dir(): ap.error(f'not a directory: {root}')
    files = sorted(root.rglob('*.md'))
    if not files: ap.error('no Markdown files')
    if out.exists() and any(out.iterdir()) and not args.url_map:
        ap.error(f'output must be empty: {out}')
    out.mkdir(parents=True, exist_ok=True)
    url_map = json.loads(args.url_map.read_text()) if args.url_map else {}
    source_keys = {p.relative_to(root).as_posix(): p for p in files}
    docs, edges, images, errors = [], [], [], []

    for p in files:
        rel = p.relative_to(root).as_posix(); text = p.read_text(encoding='utf-8')
        bad = CONTROL.search(text)
        if bad and not args.allow_control_chars:
            errors.append({'file': rel, 'error': f'control character U+{ord(bad.group()):04X} at offset {bad.start()}'})
        refs = []
        for m in MD_LINK.finditer(text):
            target_raw, fragment = split_dest(m.group(2))
            target = resolve(p.parent, target_raw)
            if target and target.suffix.lower() == '.md' and target in files:
                target_rel = target.relative_to(root).as_posix()
                refs.append(target_rel); edges.append({'from': rel, 'to': target_rel, 'fragment': fragment})
        for pattern, kind in ((MD_IMAGE, 'markdown'), (WIKI_IMAGE, 'wikilink')):
            for m in pattern.finditer(text):
                raw = m.group(2 if kind == 'markdown' else 1); target = resolve(p.parent, raw)
                if target and target.exists() and target.is_file():
                    marker = f'LOCAL_IMAGE_{hashlib.sha256((rel + "\\0" + str(target)).encode()).hexdigest()[:16]}'
                    images.append({'document': rel, 'source': str(target), 'syntax': kind,
                                   'alt': m.group(1) if kind == 'markdown' else '', 'marker': marker})
                elif not re.match(r'https?://', raw.strip()):
                    errors.append({'file': rel, 'error': f'unresolved image: {raw}'})
        docs.append({'path': rel, 'title': p.stem, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'references': sorted(set(refs))})

    report = {'documents': docs, 'edges': edges, 'images': images, 'errors': errors,
              'url_map_complete': len(url_map) == len(files) and set(url_map) == set(source_keys)}
    (out/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    if not args.url_map:
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 1 if errors else 0

    missing = sorted(set(source_keys)-set(url_map)); extra = sorted(set(url_map)-set(source_keys))
    if missing or extra:
        print(json.dumps({'missing_url_map': missing, 'extra_url_map': extra}, ensure_ascii=False, indent=2), file=sys.stderr); return 2
    for p in files:
        rel=p.relative_to(root).as_posix(); text=p.read_text(encoding='utf-8')
        def rewrite(m: re.Match[str]) -> str:
            label, dest=m.group(1), m.group(2); target_raw, fragment=split_dest(dest); target=resolve(p.parent,target_raw)
            if target and target.suffix.lower()=='.md' and target in files:
                url=url_map[target.relative_to(root).as_posix()]
                # Feishu document URLs have no stable Markdown heading anchors; preserve label, omit source fragment.
                return f'[{label}]({url})'
            return m.group(0)
        staged=MD_LINK.sub(rewrite,text)
        image_markers = {(item['document'], Path(item['source']).resolve()): item['marker'] for item in images}
        def replace_image(match: re.Match[str], kind: str) -> str:
            raw = match.group(2 if kind == 'markdown' else 1)
            target = resolve(p.parent, raw)
            marker = image_markers.get((rel, target)) if target else None
            return marker if marker else match.group(0)
        staged = MD_IMAGE.sub(lambda m: replace_image(m, 'markdown'), staged)
        staged = WIKI_IMAGE.sub(lambda m: replace_image(m, 'wikilink'), staged)
        dst=out/'markdown'/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_text(staged,encoding='utf-8')
    print(json.dumps({'staged': len(files), 'images': len(images), 'errors': errors}, ensure_ascii=False))
    return 1 if errors else 0
if __name__ == '__main__': raise SystemExit(main())
