#!/usr/bin/env python3
"""Scan distributable MCP/Skill sources; report locations and types, never values."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

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
    'Gemini connector ID': re.compile(r'\buser_bound_custom-mcp-[0-9a-fA-F]{16,}\b'),
}
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')
EXAMPLE_DOMAINS = {'example.com', 'example.net', 'example.org', 'example.test'}
MCP_REQUIRED_FILES = {
    'mcp/DEPLOYMENT.md', 'mcp/README.md', 'mcp/scripts/linux.sh',
    'mcp/brave-search-mcp/server.py', 'mcp/colab-cli-mcp/server.py', 'mcp/exa-mcp/server.py',
    'mcp/firecrawl-mcp/server.py', 'mcp/lark-cli-mcp/server.py',
    'mcp/lark-doc/scripts/mcp_server.py',
}
MCP_RUNTIME_DIRS = {
    '.cache', '.fastmcp', '.firecrawl', '.git', '.lark_publish', '.pytest_cache',
    '.venv', '__pycache__', 'lark-state', 'redis-data',
}


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


def scan_sources():
    files = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'mcp', 'skills'], cwd=ROOT).decode().split('\0')
    checked = found = 0
    for name in sorted(set(files)):
        path = ROOT/name
        if not name:
            continue
        if path.is_symlink():
            print(f'{name}: symlink requires manual review')
            found += 1
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        checked += 1
        for line, kind in findings(text):
            print(f'{name}:{line}: {kind}')
            found += 1
    return checked, found


def is_runtime_path(path):
    name = path.name
    env_file = name in {'.env', '.envrc'} or name.startswith(('.env.', '.envrc.')) or name.endswith('.env')
    if path.parts[1:2] == ('python',):
        return True
    if len(path.parts) >= 4 and path.parts[2] == 'deploy':
        record = path.parts[3]
        if (record == 'python' or record.endswith('.version')
                or record.startswith(('python.backup-', '.python-candidate-', '.lark-cli-version.'))):
            return True
    return (
        any(part in MCP_RUNTIME_DIRS for part in path.parts)
        or env_file and not name.endswith('.example')
        or name == 'auth.token'
        or name.startswith(('.apikey.', '.feishu-env-'))
        or path.suffix in {'.key', '.pem', '.p12', '.pfx', '.bak'}
    )


def scan_mcp_archive(archive_path):
    seen = set()
    files = set()
    checked = found = 0
    with tarfile.open(archive_path, 'r|*') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.parts[:1] != ('mcp',) or '..' in path.parts
                    or member.name != path.as_posix() or path in seen):
                print('Archive contains an invalid or duplicate MCP path.')
                found += 1
                continue
            seen.add(path)
            if is_runtime_path(path):
                print(f'{path}: runtime file or deployment record')
                found += 1
                continue
            if member.isdir():
                continue
            if not member.isfile():
                print(f'{path}: archive entry is not a regular file')
                found += 1
                continue
            files.add(str(path))
            try:
                with archive.extractfile(member) as source:
                    content = source.read().decode('utf-8')
            except UnicodeDecodeError:
                print(f'{path}: non-UTF-8 file requires manual review')
                found += 1
                continue
            if '\0' in content:
                print(f'{path}: binary file requires manual review')
                found += 1
                continue
            checked += 1
            for line, kind in findings(content):
                print(f'{path}:{line}: {kind}')
                found += 1
    for name in sorted(MCP_REQUIRED_FILES - files):
        print(f'{name}: missing required distribution file')
        found += 1
    return checked, found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='check a tar archive containing the distributable mcp/ tree')
    args = parser.parse_args()
    try:
        checked, found = scan_mcp_archive(args.archive) if args.archive else scan_sources()
    except (OSError, EOFError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f'Source check failed ({type(exc).__name__}).', file=sys.stderr)
        return 1
    print(f'Checked {checked} source files; {found} potential private-information or packaging findings.')
    print('Heuristic scan only; review credentials and identifiers in the final diff before publishing.')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
