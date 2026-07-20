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
    def test_skill_routes_daily_use_away_from_server_setup(self) -> None:
        root = SCRIPTS.parent
        skill = (root / "SKILL.md").read_text()
        readme = (root / "README.md").read_text()
        publish = (root / "references" / "markdown-publish.md").read_text()

        self.assertIn("日常文档模式（默认）", skill)
        self.assertIn("服务器配置模式（仅显式触发）", skill)
        self.assertIn("只调用已连接的 `Lark-Markdown` MCP 工具", skill)
        self.assertNotIn("uv run python scripts/mcp_server.py", skill)
        self.assertNotIn("codex mcp add", skill)
        self.assertIn("Agent 即使正在协助部署，也只能给出命令，不得运行", skill)
        self.assertIn("https://lark-markdown.nexuszone.link/mcp", readme)
        self.assertIn("两阶段", publish)
        self.assertIn("循环引用", publish)

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
                "一个断言[^1]。\n\n[^1]: [来源](https://example.com)\n"
                "    补充说明\n\n`[^literal]`\n\n"
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
            self.assertIn("一个断言[1]。\n\n> [1] [来源](https://example.com)\n> 补充说明", rendered)
            self.assertIn("`[^literal]`", rendered)
            self.assertIn("$$literal$$", rendered)

            nodes = {"test": "https://example/test", "test/sub": "https://example/sub"}
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
            # Root index links to the sub-folder node, not to its documents.
            root_index = (out / "folder-indexes" / "index.md").read_text()
            self.assertIn("https://example/sub", root_index)
            self.assertIn("[sub]", root_index)
            self.assertNotIn("https://example/b", root_index)
            # Sub-folder index lists its direct documents by name (no path prefix).
            sub_index = (out / "folder-indexes" / "sub.md").read_text()
            self.assertIn("https://example/b", sub_index)
            self.assertIn("[b(test)]", sub_index)
            self.assertNotIn("sub/b(test)", sub_index)

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

            remote_index = {
                path: {"doc": url, "revision_id": index + 10}
                for index, (path, url) in enumerate(url_map.items())
            }
            (out / "remote-index.json").write_text(json.dumps(remote_index))
            run("commit_publish_state.py", "--workdir", ".lark_publish", cwd=cwd)
            committed = json.loads((out / "state.json").read_text())
            self.assertEqual(set(committed["documents"]), set(url_map))
            self.assertEqual(json.loads((out / "report.json").read_text())["status"], "success")

            run("cleanup_workspace.py", "--workdir", ".lark_publish", cwd=cwd)
            self.assertEqual(
                sorted(path.name for path in out.iterdir()),
                ["report.json", "state.json", "url-map.json"],
            )
            run("prepare_publish.py", "knowledge-base/test", "--out", ".lark_publish", cwd=cwd)
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "state.json").exists())

    def test_formula_converter_rejects_overlapping_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "note.md").write_text("$$x$$")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "center_display_math.py"), str(source), str(source)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((source / "note.md").exists())

    def test_failed_commit_preserves_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            workdir = cwd / ".lark_publish"
            workdir.mkdir()
            previous = {"version": 1, "documents": {"a.md": {"sha256": "old"}}}
            (workdir / "state.json").write_text(json.dumps(previous))
            (workdir / "manifest.json").write_text(json.dumps({
                "documents": [{"path": "a.md", "sha256": "new"}],
            }))
            (workdir / "url-map.json").write_text(json.dumps({"a.md": "https://example/a"}))
            (workdir / "remote-index.json").write_text(json.dumps({"a.md": {}}))

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "commit_publish_state.py")],
                cwd=cwd, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads((workdir / "state.json").read_text()), previous)
            self.assertEqual(json.loads((workdir / "report.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
