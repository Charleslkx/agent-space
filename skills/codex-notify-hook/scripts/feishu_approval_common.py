#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


CODEX_DIR = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
ENV_PATH = CODEX_DIR / "feishu-agent.env"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    env.update({k: v for k, v in os.environ.items() if k.startswith("FEISHU_")})
    return env
