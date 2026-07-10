#!/usr/bin/env python3
"""Install the bundled Typora Pandoc PDF configuration."""

import argparse
import shutil
from pathlib import Path


FILES = ("header.tex", "after.tex", "wrap-tables.lua")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path.home() / ".typora")
    parser.add_argument("--force", action="store_true", help="replace existing files")
    args = parser.parse_args()

    source = Path(__file__).resolve().parents[1] / "assets"
    destination = args.config_dir.expanduser()
    existing = [destination / name for name in FILES if (destination / name).exists()]
    if existing and not args.force:
        names = ", ".join(str(path) for path in existing)
        raise SystemExit(f"Refusing to overwrite: {names}. Re-run with --force.")

    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = destination / name
        shutil.copyfile(source / name, target)
        print(target)


if __name__ == "__main__":
    main()
