#!/usr/bin/env python3
"""Classify managed documents before a remote-to-local pull."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--remote-index", type=Path, required=True,
                    help="JSON object: relative path -> fetched remote revision")
parser.add_argument("--local-root", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
state = json.loads(args.state.read_text()).get("documents", {})
remote = json.loads(args.remote_index.read_text())
root = args.local_root.resolve()
plan = {"pull": [], "local_only": [], "conflicts": [], "unchanged": [], "new_remote": [], "missing_remote": [], "invalid_paths": []}
for path, item in state.items():
    local = (root / path).resolve()
    if not local.is_relative_to(root):
        plan["invalid_paths"].append(path); continue
    local_sha = hashlib.sha256(local.read_bytes()).hexdigest() if local.exists() else None
    local_changed = local_sha != item.get("sha256")
    fetched = remote.get(path)
    if fetched is None:
        plan["missing_remote"].append(path); continue
    remote_changed = fetched["revision_id"] != item.get("remote_revision")
    if remote_changed and local_changed: plan["conflicts"].append(path)
    elif remote_changed: plan["pull"].append(path)
    elif local_changed: plan["local_only"].append(path)
    else: plan["unchanged"].append(path)
for path in remote:
    if path not in state: plan["new_remote"].append(path)
args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: len(value) for key, value in plan.items()}, ensure_ascii=False))
