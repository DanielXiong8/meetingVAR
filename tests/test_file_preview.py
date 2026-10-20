"""Project file preview API tests: text/texture/image/binary and path safety."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "desktop"))

import engine_bridge  # noqa: E402


class FilePreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mv_preview_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "README.md").write_text("# 项目\n说明内容", encoding="utf-8")
        (self.root / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.root / "img.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "a.tsv").write_text("a\tb\n", encoding="utf-8")
        (self.root.parent / "outside_preview.txt").write_text("secret", encoding="utf-8")

    def test_text_and_code_preview(self):
        text = engine_bridge.cmd_read_project_file(self.root, "README.md")
        self.assertTrue(text["ok"])
        self.assertEqual(text["kind"], "text")
        self.assertIn("说明内容", text["text"])
        code = engine_bridge.cmd_read_project_file(self.root, "app.py")
        self.assertEqual(code["kind"], "text")
        self.assertEqual(code["language"], "python")

    def test_nested_relative_path(self):
        result = engine_bridge.cmd_read_project_file(self.root, "sub/a.tsv")
        self.assertTrue(result["ok"])
        self.assertIn("a\tb", result["text"])

    def test_image_data_url(self):
        result = engine_bridge.cmd_read_project_file(self.root, "img.png")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "image")
        self.assertTrue(result["data_url"].startswith("data:image/png;base64,"))

    def test_binary_metadata_only(self):
        result = engine_bridge.cmd_read_project_file(self.root, "blob.bin")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "binary")
        self.assertNotIn("text", result)

    def test_path_traversal_is_rejected(self):
        for bad in ("../outside_preview.txt", "sub/../../outside_preview.txt",
                    "/etc/passwd", "C:/Windows/win.ini"):
            with self.subTest(path=bad):
                result = engine_bridge.cmd_read_project_file(self.root, bad)
                self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()