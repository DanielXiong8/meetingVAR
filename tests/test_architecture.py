"""Project architecture: deterministic map projection plus editable AI draft."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.architecture import (architecture, architecture_view,
                                           generate_architecture, save_architecture)
from meetingvar.memory.repository_map import build_map


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_arch_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "src" / "meetingvar" / "agent").mkdir(parents=True)
        (self.root / "src" / "meetingvar" / "memory").mkdir(parents=True)
        (self.root / "src" / "meetingvar" / "agent" / "loop.py").write_text(
            "from meetingvar.memory import store\n\ndef run():\n    return store\n", encoding="utf-8")
        (self.root / "src" / "meetingvar" / "memory" / "__init__.py").write_text(
            "VALUE = 1\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_loop.py").write_text("def test():\n    pass\n", encoding="utf-8")
        (self.root / "desktop").mkdir()
        (self.root / "desktop" / "app.py").write_text("import meetingvar.agent.loop\n", encoding="utf-8")

    def test_groups_layers_and_dependency_edges(self):
        build_map(self.root)
        report = architecture(self.root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["architecture_status"], "complete")
        groups = {row["id"]: row for row in report["groups"]}
        self.assertIn("src/meetingvar/agent", groups)
        self.assertIn("src/meetingvar/memory", groups)
        self.assertEqual(groups["src/meetingvar/agent"]["layer"], "core")
        self.assertEqual(groups["tests"]["layer"], "test")
        self.assertIn("desktop", groups)
        self.assertEqual(groups["desktop"]["layer"], "entry")
        edges = {(row["source"], row["target"]) for row in report["edges"]}
        self.assertIn(("src/meetingvar/agent", "src/meetingvar/memory"), edges)
        self.assertIn(("desktop", "src/meetingvar/agent"), edges)
        self.assertEqual(report["coverage"]["exhaustive"], False)

    def test_not_built_is_explicit(self):
        report = architecture(self.root)
        self.assertEqual(report["architecture_status"], "not_built")
        self.assertEqual(report["groups"], [])

    def test_root_files_are_not_one_module_each(self):
        (self.root / "models.json").write_text('{"model": "demo"}\n', encoding="utf-8")
        (self.root / "notes.md").write_text("# Notes\n", encoding="utf-8")
        build_map(self.root)
        report = architecture(self.root)
        groups = {row["id"]: row for row in report["groups"]}
        self.assertNotIn("models.json", groups)
        self.assertIn("根目录·配置", groups)
        self.assertIn("根目录·文档", groups)
        self.assertEqual(groups["根目录·配置"]["layer"], "data")
        self.assertEqual(groups["根目录·文档"]["layer"], "docs")

    def test_codeowners_and_manual_owner_persist(self):
        codeowners = self.root / ".github" / "CODEOWNERS"
        codeowners.parent.mkdir()
        codeowners.write_text("/src/meetingvar/agent/ @alice\n", encoding="utf-8")
        build_map(self.root)
        view = architecture_view(self.root)
        groups = {row["id"]: row for row in view["groups"]}
        self.assertEqual(groups["src/meetingvar/agent"]["owner"], "alice")

        saved = save_architecture(self.root, {
            "modules": [{"id": "manual-ops", "name": "部署与运维", "purpose": "发布和回滚",
                         "layer": "entry", "files": ["desktop/app.py"], "owner": "李四"}],
            "owners": {"src/meetingvar/agent": "张三"},
        })
        self.assertTrue(saved["ok"], saved)
        groups = {row["id"]: row for row in saved["groups"]}
        self.assertEqual(groups["src/meetingvar/agent"]["owner"], "张三")
        self.assertEqual(groups["manual-ops"]["owner"], "李四")
        self.assertEqual(groups["manual-ops"]["source"], "user")
        self.assertTrue((self.root / "meetingvar" / "agent_memory" / "architecture.json").is_file())

        cleared = save_architecture(self.root, {
            "modules": [{"id": "manual-ops", "name": "部署与运维", "layer": "entry",
                         "files": ["desktop/app.py"], "owner": "李四"}],
            "owners": {"src/meetingvar/agent": ""},
        })
        groups = {row["id"]: row for row in cleared["groups"]}
        self.assertEqual(groups["src/meetingvar/agent"]["owner"], "未指定")

    def test_ai_draft_keeps_manual_modules_and_rejects_bad_json(self):
        build_map(self.root)

        def fake(prompt: str) -> dict:
            self.assertIn("仓库地图", prompt)
            return {"ok": True, "model": "fake-model", "content": json.dumps({
                "summary": "演示项目的两个核心模块",
                "modules": [
                    {"id": "agent-core", "name": "代理循环", "purpose": "执行工具循环",
                     "layer": "core", "files": ["src/meetingvar/agent/loop.py"],
                     "owner": "张三", "confidence": 0.9},
                    {"id": "memory-core", "name": "记忆存储", "purpose": "保存项目记忆",
                     "layer": "core", "files": ["src/meetingvar/memory/__init__.py"]},
                ],
                "edges": [{"source": "agent-core", "target": "memory-core", "label": "读取记忆"}],
                "unassigned": ["README.md"],
            }, ensure_ascii=False)}

        report = generate_architecture(self.root, fake, model="fake-model", max_digest_chars=20000)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["generation"]["mode"], "ai")
        self.assertEqual(report["generation"]["model"], "fake-model")
        groups = {row["id"]: row for row in report["groups"]}
        self.assertEqual(groups["agent-core"]["name"], "代理循环")
        # The invented owner is not in the material, so it must be left blank.
        self.assertEqual(groups["agent-core"]["owner"], "未指定")
        self.assertTrue(report["edges"])

        save_architecture(self.root, {
            "modules": [{"id": "manual-qa", "name": "质量保障", "layer": "test",
                         "files": ["tests/test_loop.py"], "owner": "王五"}],
            "owners": {},
        })
        again = generate_architecture(self.root, fake, model="fake-model", max_digest_chars=20000)
        self.assertTrue(again["ok"], again)
        manual = {row["id"]: row for row in again["manual_modules"]}
        self.assertIn("manual-qa", manual)
        self.assertEqual(manual["manual-qa"]["owner"], "王五")

        def bad(prompt: str) -> dict:
            return {"ok": True, "content": "这不是 JSON"}

        failed = generate_architecture(self.root, bad, model="fake-model", max_digest_chars=20000)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error_code"], "invalid_architecture")
        still = architecture_view(self.root)
        self.assertEqual(still["generation"]["mode"], "ai")
        self.assertIn("agent-core", {row["id"] for row in still["groups"]})


if __name__ == "__main__":
    unittest.main()
