#!/usr/bin/env python3
"""Commit verified remote revisions to state.json and report.json."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, default=Path(".lark_publish"))
    args = parser.parse_args()
    workdir = args.workdir
    if workdir.name != ".lark_publish" or workdir.is_symlink():
        parser.error("workdir must be a non-symlink directory named .lark_publish")
    manifest = json.loads((workdir / "manifest.json").read_text())
    url_map = json.loads((workdir / "url-map.json").read_text())
    remote = json.loads((workdir / "remote-index.json").read_text())
    documents = {item["path"]: item for item in manifest["documents"]}
    errors = list(manifest.get("errors", []))
    for path in sorted(set(documents) | set(url_map) | set(remote)):
        if path not in documents or path not in url_map or path not in remote:
            errors.append({"file": path, "error": "manifest, URL map, and remote index must contain identical paths"})
        elif not isinstance(remote[path], dict):
            errors.append({"file": path, "error": "remote index entry must be an object"})
        elif remote[path].get("error"):
            errors.append({"file": path, "error": remote[path]["error"]})
        elif remote[path].get("revision_id") is None:
            errors.append({"file": path, "error": "missing verified revision_id"})
    report = {
        "status": "failed" if errors else "success",
        "document_count": len(documents),
        "errors": errors,
    }
    atomic_json(workdir / "report.json", report)
    if errors:
        return 1
    state_documents = {}
    for path, item in documents.items():
        identifier = remote[path].get("doc_token") or remote[path].get("doc") or url_map[path]
        state_documents[path] = {
            "sha256": item["sha256"],
            "url": url_map[path],
            "doc_token": identifier.rstrip("/").rsplit("/", 1)[-1],
            "remote_revision": remote[path]["revision_id"],
        }
    state = {"version": 1, "documents": state_documents}
    atomic_json(workdir / "state.json", state)
    print(json.dumps({"committed": len(documents)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
