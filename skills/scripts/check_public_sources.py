#!/usr/bin/env python3
"""Scan distributable MCP/Skill sources; report locations and types, never values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    'personal absolute path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'private network address': re.compile(r'\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
    'Feishu identity': re.compile(r'\b(?:oc|ou|on|cli)_[a-fA-F0-9]{16,}\b'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'private key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'JWT': re.compile(r'\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}'),
    'private hostname': re.compile(r'\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:internal|corp|lan)\b'),
    'webhook credential': re.compile(r'https://open\.(?:feishu\.cn|larksuite\.com)/open-apis/bot/v2/hook/[A-Za-z0-9_-]{16,}'),
}
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')
EXAMPLE_DOMAINS = {'example.com', 'example.net', 'example.org', 'example.test'}


def findings(text):
    results = []
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            results.append((text.count('\n', 0, match.start()) + 1, kind))
    for match in EMAIL.finditer(text):
        domain = match.group(1).lower()
        if not (domain in EXAMPLE_DOMAINS or any(domain.endswith('.'+d) for d in EXAMPLE_DOMAINS) or domain.endswith(('.example','.test','.invalid','.localhost'))):
            results.append((text.count('\n', 0, match.start()) + 1, 'email address'))
    return sorted(set(results))


def main():
    files = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'mcp', 'skills'], cwd=ROOT).decode().split('\0')
    checked = found = 0
    for name in sorted(set(files)):
        path = ROOT/name
        if not name or not path.is_file():
            continue
        if path.is_symlink():
            print(f'{name}: symlink requires manual review')
            found += 1
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        checked += 1
        for line, kind in findings(text):
            print(f'{name}:{line}: {kind}')
            found += 1
    print(f'Checked {checked} source files; {found} potential private-information findings.')
    print('Heuristic scan only; review credentials and identifiers in the final diff before publishing.')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
