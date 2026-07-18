#!/usr/bin/env python3
"""Plan minimal safe updates from the current manifest and previous state."""
from __future__ import annotations
import argparse, json
from collections import defaultdict
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest", type=Path, required=True)
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
manifest = json.loads(args.manifest.read_text())
state = json.loads(args.state.read_text()) if args.state.exists() else {"documents": {}}
current = {doc["path"]: doc for doc in manifest["documents"]}
previous = state.get("documents", {})
new = sorted(set(current) - set(previous))
changed = sorted(path for path in set(current) & set(previous) if current[path]["sha256"] != previous[path]["sha256"])
deleted = sorted(set(previous) - set(current))
reverse = defaultdict(set)
for edge in manifest["edges"]:
    reverse[edge["to"]].add(edge["from"])
dependents = sorted({source for target in new for source in reverse[target]})
plan = {"new": new, "changed": changed, "dependents_of_new": dependents,
        "write_set": sorted(set(new) | set(changed) | set(dependents)),
        "unchanged": sorted(set(current) - set(new) - set(changed) - set(dependents)),
        "deleted_local": deleted}
args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: len(value) for key, value in plan.items()}, ensure_ascii=False))
