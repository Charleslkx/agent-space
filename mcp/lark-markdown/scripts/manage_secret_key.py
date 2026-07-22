#!/usr/bin/env python3
"""Manually create, rotate, or display a static MCP bearer token."""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import tempfile
from pathlib import Path


def _require_human(action: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("refusing non-interactive use; run this command manually in a terminal")
    phrase = f"{action.upper()} LARK-MARKDOWN SECRET"
    if input(f"Type {phrase!r} to continue: ") != phrase:
        raise SystemExit("confirmation did not match")


def _read(path: Path) -> str:
    stat = path.stat()
    if path.is_symlink() or not path.is_file():
        raise SystemExit("secret path must be a regular file, not a symlink")
    if stat.st_mode & 0o077:
        raise SystemExit("secret file permissions must be 0600 or stricter")
    secret = path.read_text().strip()
    if len(secret) < 32:
        raise SystemExit("secret is shorter than 32 characters")
    return secret


def _write(path: Path, replace: bool) -> None:
    if path.is_symlink():
        raise SystemExit("secret path must not be a symlink")
    if path.exists() != replace:
        state = "already exists; use rotate" if not replace else "does not exist; use init"
        raise SystemExit(f"{path} {state}")
    if not path.parent.is_dir():
        raise SystemExit(f"parent directory does not exist: {path.parent}")
    secret = secrets.token_urlsafe(48)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secret + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "rotate", "show"))
    parser.add_argument("path", type=Path, help="explicit secret file path")
    args = parser.parse_args()
    _require_human(args.action)
    if args.action == "show":
        print(_read(args.path))
        return
    _write(args.path, replace=args.action == "rotate")
    print(f"secret {args.action} complete: {args.path}")


if __name__ == "__main__":
    main()
