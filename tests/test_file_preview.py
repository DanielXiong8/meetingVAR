"""Project file preview API tests: text/texture/image/binary and path safety."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
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
        with zipfile.ZipFile(self.root / "links.txt", "w") as archive:
            archive.writestr("活动链接.txt", "https://example.invalid/a")
            archive.writestr("readme.md", "# nested")
        with zipfile.ZipFile(self.root / "plain.zip", "w") as archive:
            archive.writestr("inner.txt", "plain zip")
        with zipfile.ZipFile(self.root / "renamed.txt", "w") as archive:
            archive.writestr(
                "word/document.xml",
                "<document><body><p>" + "被改名的 Word 正文" + "</p></body></document>")

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

    def test_zip_disguised_as_txt_is_reported_as_zip(self):
        result = engine_bridge.cmd_read_project_file(self.root, "links.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "zip")
        self.assertEqual(result["kind_label"], "ZIP 压缩包")
        self.assertTrue(result["mismatch"])
        self.assertEqual(result["suffix"], ".txt")
        self.assertEqual(result["actual_suffix"], ".zip")
        names = {entry["name"] for entry in result["entries"]}
        self.assertIn("活动链接.txt", names)
        self.assertEqual(result["entry_count"], 2)
        self.assertNotIn("text", result)

    def test_plain_zip_has_no_mismatch(self):
        result = engine_bridge.cmd_read_project_file(self.root, "plain.zip")
        self.assertEqual(result["kind"], "zip")
        self.assertFalse(result["mismatch"])
        self.assertEqual(result["entry_count"], 1)

    def test_renamed_docx_is_read_by_content(self):
        result = engine_bridge.cmd_read_project_file(self.root, "renamed.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "docx")
        self.assertTrue(result["mismatch"])
        self.assertIn("被改名的 Word 正文", result["text"])

    def test_tree_marks_renamed_zip(self):
        nodes, _ = engine_bridge._build_tree(self.root)
        node = next(item for item in nodes if item["name"] == "links.txt")
        self.assertEqual(node["kind"], "zip")
        self.assertEqual(node["kind_short"], "ZIP")
        self.assertTrue(node["mismatch"])

    def test_legacy_office_preview_without_host_apps(self):
        fixtures = ROOT / "tests" / "fixtures" / "legacy"
        for name in ("sample.doc", "sample.ppt", "sample.xls"):
            shutil.copy2(fixtures / name, self.root / name)
        doc = engine_bridge.cmd_read_project_file(self.root, "sample.doc")
        ppt = engine_bridge.cmd_read_project_file(self.root, "sample.ppt")
        xls = engine_bridge.cmd_read_project_file(self.root, "sample.xls")
        self.assertEqual(doc["kind"], "doc")
        self.assertIn("Legacy Word 正文测试", doc["text"])
        self.assertEqual(ppt["kind"], "ppt")
        self.assertIn("Legacy PPT 标题", ppt["text"])
        self.assertEqual(xls["kind"], "xls")
        self.assertIn("负责人", xls["text"])

    def test_path_traversal_is_rejected(self):
        for bad in ("../outside_preview.txt", "sub/../../outside_preview.txt",
                    "/etc/passwd", "C:/Windows/win.ini"):
            with self.subTest(path=bad):
                result = engine_bridge.cmd_read_project_file(self.root, bad)
                self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()