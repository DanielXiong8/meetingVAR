"""健壮文本读取测试（model-free）：二进制伪装 / GBK 回退 / docx 解析 / 回归修复。

背景（2026-10-15 真机）：一个伪装成 .txt 的 zip 文件曾同时导致
① load_memory_docs 严格 utf-8 崩溃（引擎退出码 1）② 词表抽出二进制垃圾词。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.memory.extract import extract_terms  # noqa: E402
from meetingvar.memory.files import read_text_safely  # noqa: E402


class SafeReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_safe_"))

    def test_zip_disguised_as_txt_returns_none(self) -> None:
        p = self.tmp / "活动链接.txt"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", "<w:p>体育建设</w:p>")
        self.assertIsNone(read_text_safely(p), "zip 魔数的伪装 txt 必须判为二进制")

    def test_nul_binary_returns_none(self) -> None:
        p = self.tmp / "bin.txt"
        p.write_bytes(b"abc\x00def")
        self.assertIsNone(read_text_safely(p))

    def test_gbk_text_round_trip(self) -> None:
        p = self.tmp / "gbk.txt"
        p.write_bytes("中文内容测试".encode("gbk"))
        self.assertEqual(read_text_safely(p), "中文内容测试")

    def test_utf8_text_normal(self) -> None:
        p = self.tmp / "utf8.txt"
        p.write_text("正常 UTF-8", encoding="utf-8")
        self.assertEqual(read_text_safely(p), "正常 UTF-8")

    def test_docx_text_extracted(self) -> None:
        p = self.tmp / "材料.docx"
        xml = (
            '<?xml version="1.0"?><w:document><w:body>'
            "<w:p><w:r><w:t>体育建设书面材料</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>第二段内容</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", xml)
        text = read_text_safely(p)
        self.assertIsNotNone(text)
        self.assertIn("体育建设书面材料", text)
        self.assertIn("第二段内容", text)

    def test_plain_zip_without_docx_returns_none(self) -> None:
        p = self.tmp / "归档.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("a.txt", "hello")
        self.assertIsNone(read_text_safely(p), "非 docx 的 zip 不解析（避免误读）")

    def test_extract_terms_skips_disguised_zip(self) -> None:
        (self.tmp / "real.md").write_text("# 体育建设\n- 运动会：年度活动\n", encoding="utf-8")
        bad = self.tmp / "链接.txt"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("[Content_Types].xml", "<Types>junk</Types>")
            z.writestr("_rels/.rels", "<Relationships/>")
        terms = extract_terms(self.tmp)
        for junk in ("Content", "[Content_Types]", "CQ", "Dc", "Fk"):
            self.assertNotIn(junk, terms, f"二进制垃圾词 {junk} 不应进词表")

    def test_load_memory_docs_survives_binary(self) -> None:
        from meetingvar.cli import load_memory_docs

        (self.tmp / "a.md").write_text("正常文档", encoding="utf-8")
        (self.tmp / "b.txt").write_bytes(b"PK\x03\x04" + b"\xd2" * 64)
        docs = load_memory_docs(self.tmp)  # 修复前这里抛 UnicodeDecodeError
        self.assertEqual(len(docs), 1)


if __name__ == "__main__":
    unittest.main()
