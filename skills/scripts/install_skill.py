#!/usr/bin/env python3
"""Install a complete Skill without copying runtime secrets or changing hooks."""
from __future__ import annotations
import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PARENTS = {
    'codex': ('.agents/skills', '.agents/skills'),
    'claude': ('.claude/skills', '.claude/skills'),
    'cursor': ('.cursor/skills', '.cursor/skills'),
    'copilot': ('.copilot/skills', '.github/skills'),
    'opencode': ('.config/opencode/skills', '.opencode/skills'),
}
SKILL_ROOT = Path(__file__).resolve().parents[1]


def ignore_runtime(directory, names):
    excluded = {'.venv', '__pycache__', 'node_modules', '.lark_publish', '.DS_Store', '.git',
                '.firecrawl', '.fastmcp', 'lark-state', 'redis-data', 'auth.json', 'credentials.json'}
    return [name for name in names if name in excluded or
            name.endswith(('.pyc', '.log', '.token', '.key', '.pem', '.pfx', '.p12', '.bak', '.tmp', '.sqlite', '.sqlite3', '.sqlite-wal', '.sqlite-shm', '.db', '.rdb', '.aof')) or
            (name.endswith('.env') or name.startswith('.env')) and not name.endswith('.example')]


def install(source: Path, parent: Path, upgrade: bool = False) -> Path:
    source = source.resolve()
    parent = parent.expanduser().absolute()
    target = parent / source.name
    if not (source / 'SKILL.md').is_file():
        raise ValueError('source must be a complete Skill directory')
    if target.resolve() == source or source in parent.resolve().parents or parent.resolve() == source:
        raise ValueError('installation destination cannot be inside the source Skill')
    # Distributed skills must be self-contained, not links to a private workspace.
    for directory, folders, files in os.walk(source):
        excluded = set(ignore_runtime(directory, folders + files))
        folders[:] = [name for name in folders if name not in excluded]
        if any((Path(directory)/name).is_symlink() for name in folders+files if name not in excluded):
            raise ValueError('source Skill must not contain distributable symlinks')
    if target.is_symlink():
        raise ValueError('refusing to replace a symlinked installation')
    if target.exists() and not upgrade:
        raise FileExistsError('skill already exists; inspect it and use --upgrade to back it up')
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.skill-install-', dir=parent) as staging:
        staged = Path(staging) / source.name
        shutil.copytree(source, staged, ignore=ignore_runtime)
        if target.exists():
            backup_root = parent.parent / 'skill-backups'
            backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup_root.chmod(0o700)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = backup_root / f'{source.name}-{stamp}'
            target.rename(backup)
            try:
                staged.rename(target)
            except OSError:
                backup.rename(target)
                raise
        else:
            staged.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill', required=True, choices=sorted(p.name for p in SKILL_ROOT.iterdir() if (p/'SKILL.md').is_file()))
    parser.add_argument('--agent', required=True, choices=PARENTS)
    parser.add_argument('--project', type=Path, help='project root; omitted for current-user installation')
    parser.add_argument('--upgrade', action='store_true', help='back up an existing Skill then replace it')
    args = parser.parse_args()
    if args.project and not args.project.expanduser().is_dir():
        parser.error('--project must identify an existing project directory')
    checked = subprocess.run(['bash', str(SKILL_ROOT/'scripts/check-python.sh'), '--runtime'], env={**os.environ, 'PYTHON_BIN': sys.executable})
    if checked.returncode:
        parser.exit(checked.returncode, 'Resolve Python prerequisites before installing a Skill.\n')
    global_parent, project_parent = PARENTS[args.agent]
    parent = args.project.expanduser().resolve()/project_parent if args.project else Path.home()/global_parent
    try:
        target = install(SKILL_ROOT/args.skill, parent, args.upgrade)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Installation failed: {error}\n')
    print(f'Installed {args.skill}; registration location: {PARENTS[args.agent][1 if args.project else 0]}')
    print('Reload the client, then follow SKILL.md for dependency and hook configuration.')


if __name__ == '__main__':
    main()
