#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


CODEX_DIR = Path(os.environ.get("CODEX_HOME", "/Users/charles/.codex"))
ENV_PATH = CODEX_DIR / "feishu-agent.env"
STATE_DIR = CODEX_DIR / "feishu-approvals"
PENDING_DIR = STATE_DIR / "pending"
RESULT_DIR = STATE_DIR / "results"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    env.update({k: v for k, v in os.environ.items() if k.startswith("FEISHU_")})
    return env


def set_env_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    replaced = False
    out = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n")
    path.chmod(0o600)


def ensure_state_dirs() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def now() -> int:
    return int(time.time())
