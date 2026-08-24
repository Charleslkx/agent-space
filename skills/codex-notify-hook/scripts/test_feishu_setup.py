#!/usr/bin/env python3
"""Small runnable check for independent setup, reuse, and creation."""

from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile


SCRIPT = Path(__file__).with_name("create_feishu_agent_app.py")


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env={**os.environ, **env},
        check=False,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        peer = root / "peer.env"
        target = root / "target.env"
        peer.write_text(
            "FEISHU_APP_ID=cli_reused\n"
            "FEISHU_APP_SECRET=secret\n"
            "FEISHU_HOME_CHANNEL=oc_reused\n"
            "FEISHU_APPROVAL_RECEIVE_ID_TYPE=chat_id\n"
        )
        reused = run(
            ["--env-out", str(target)],
            {"FEISHU_REUSE_ENV_PATHS": str(peer)},
        )
        assert reused.returncode == 0, reused.stderr
        assert "Reused app cli_reused" in reused.stdout
        assert "FEISHU_APP_SECRET=secret" in target.read_text()
        assert "FEISHU_APPROVAL_RECEIVE_ID=oc_reused" in target.read_text()
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

        fake_sdk = root / "lark_oapi.py"
        fake_sdk.write_text(
            "def register_app(**kwargs):\n"
            "    return {'client_id':'cli_new','client_secret':'new_secret',"
            "'user_info':{'tenant_brand':'feishu'}}\n"
        )
        created = root / "created.env"
        creation = run(
            ["--live", "--new", "--home-channel", "oc_new", "--env-out", str(created)],
            {"PYTHONPATH": str(root), "FEISHU_REUSE_ENV_PATHS": ""},
        )
        assert creation.returncode == 0, creation.stderr
        text = created.read_text()
        assert "FEISHU_APP_ID=cli_new" in text
        assert "FEISHU_HOME_CHANNEL=oc_new" in text
        assert "FEISHU_APPROVAL_RECEIVE_ID=oc_new" in text
        assert stat.S_IMODE(created.stat().st_mode) == 0o600

        missing = run(
            ["--live", "--new", "--env-out", str(root / "missing.env")],
            {"PYTHONPATH": str(root), "FEISHU_REUSE_ENV_PATHS": ""},
        )
        assert missing.returncode == 2
        assert "--home-channel is required" in missing.stderr

    print("ok: independent setup, automatic reuse, and fresh creation")


if __name__ == "__main__":
    main()
