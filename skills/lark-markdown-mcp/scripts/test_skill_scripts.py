#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent


def run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


class SkillScriptsTest(unittest.TestCase):
    def test_full_local_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            root = cwd / "knowledge-base" / "test"
            (root / "sub").mkdir(parents=True)
            (root / "sub" / "b(test).md").write_text("# B\n\n## part\n")
            (root / "sub" / "a.md").write_text("# duplicate title\n")
            (root / "a.md").write_text(
                "# A\n\n[go](sub/b(test).md#part \"title\")\n\n"
                "[[sub/b(test)#part|wiki go]]\n\n"
                "[reference]: <sub/b(test).md#part>\n\n"
                "`[literal](sub/b(test).md)`\n\n$$x+y$$\n\n"
                "```text\n[code](sub/b(test).md)\n$$literal$$\n```\n\n![pixel](pixel.png)\n"
            )
            (root / "pixel.png").write_bytes(
                bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")
            )

            out = cwd / ".lark_publish"
            run("prepare_publish.py", "knowledge-base/test", "--out", ".lark_publish", cwd=cwd)
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(len(manifest["documents"]), 3)
            self.assertEqual(len(manifest["edges"]), 3)
            self.assertEqual(len(manifest["images"]), 1)
            self.assertEqual(manifest["duplicate_titles"][0]["title"], "a")

            url_map = {
                "a.md": "https://example/a", "sub/a.md": "https://example/sub-a",
                "sub/b(test).md": "https://example/b",
            }
            (out / "url-map.json").write_text(json.dumps(url_map))
            run(
                "prepare_publish.py", "knowledge-base/test", "--out", ".lark_publish",
                "--url-map", ".lark_publish/url-map.json", cwd=cwd,
            )
            staged = (out / "markdown" / "a.md").read_text()
            self.assertIn('[go](https://example/b "title")', staged)
            self.assertIn("[wiki go](https://example/b)", staged)
            self.assertIn("[reference]: <https://example/b>", staged)
            self.assertIn("`[literal](sub/b(test).md)`", staged)
            self.assertIn("[code](sub/b(test).md)", staged)
            self.assertIn("LOCAL_IMAGE_", staged)

            run(
                "center_display_math.py", ".lark_publish/markdown",
                ".lark_publish/markdown-rendered", cwd=cwd,
            )
            rendered = (out / "markdown-rendered" / "a.md").read_text()
            self.assertIn(
                '<p align="center"><latex>x+y</latex></p>',
                rendered,
            )
            self.assertIn("$$literal$$", rendered)

            nodes = {"test": {}, "test/sub": {}}
            docs = {
                "a.md": {"url": "https://example/a"},
                "sub/a.md": {"url": "https://example/sub-a"},
                "sub/b(test).md": {"url": "https://example/b"},
            }
            (out / "nodes.json").write_text(json.dumps(nodes))
            (out / "docs.json").write_text(json.dumps(docs))
            run(
                "build_folder_indexes.py", "--root", "knowledge-base/test", "--label", "test",
                "--nodes", ".lark_publish/nodes.json", "--docs", ".lark_publish/docs.json",
                "--out", ".lark_publish/folder-indexes", cwd=cwd,
            )
            self.assertIn("https://example/b", (out / "folder-indexes" / "index.md").read_text())

            state = {"documents": {"a.md": {"sha256": "old", "remote_revision": 1}}}
            (out / "state.json").write_text(json.dumps(state))
            run(
                "plan_incremental.py", "--manifest", ".lark_publish/manifest.json",
                "--state", ".lark_publish/state.json", "--out", ".lark_publish/incremental-plan.json",
                cwd=cwd,
            )
            incremental = json.loads((out / "incremental-plan.json").read_text())
            self.assertEqual(incremental["new"], ["sub/a.md", "sub/b(test).md"])
            self.assertIn("a.md", incremental["changed"])

            state["documents"]["a.md"]["sha256"] = manifest["documents"][0]["sha256"]
            (out / "state.json").write_text(json.dumps(state))
            (out / "remote-index.json").write_text(json.dumps({"a.md": {"revision_id": 2}}))
            run(
                "plan_pull.py", "--state", ".lark_publish/state.json",
                "--remote-index", ".lark_publish/remote-index.json",
                "--local-root", "knowledge-base/test", "--out", ".lark_publish/pull-plan.json",
                cwd=cwd,
            )
            self.assertEqual(json.loads((out / "pull-plan.json").read_text())["pull"], ["a.md"])

            run("cleanup_workspace.py", "--workdir", ".lark_publish", cwd=cwd)
            self.assertEqual(
                sorted(path.name for path in out.iterdir()),
                ["state.json", "url-map.json"],
            )


if __name__ == "__main__":
    unittest.main()
