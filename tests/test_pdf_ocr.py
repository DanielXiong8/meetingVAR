"""Scanned-PDF fallback: OCR only when no text layer exists."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.memory import files


class PdfOcrTests(unittest.TestCase):
    def test_chinese_glyph_spaces_and_lines_are_normalized(self):
        raw = '项 目 验 收 报 告\n负 责 人 : 张 三\n计 划 周 五 交 付 第 一 版 。\n'
        cleaned = files._clean_ocr_text(raw)
        self.assertEqual(cleaned,
                         '项目验收报告\n负责人:张三\n计划周五交付第一版。')

    def test_image_only_pdf_preview_delegates_to_structured_reader(self):
        with patch.object(files, 'read_document', return_value={
                'text': '扫描件原文内容足够长', 'complete': False}) as reader:
            text = files._read_pdf(Path('synthetic-scan.pdf'))
        self.assertIn('扫描件原文内容足够长', text)
        reader.assert_called_once_with(Path('synthetic-scan.pdf'))

    def test_text_pdf_preview_does_not_force_ocr(self):
        with patch.object(files, 'read_document', return_value={
                'text': '这是有文字层的 PDF，无需 OCR 识别。', 'complete': True}) as reader, \
             patch.object(files, '_ocr_pdf') as ocr:
            text = files._read_pdf(Path('synthetic-text.pdf'))
        self.assertIn('无需 OCR', text)
        reader.assert_called_once_with(Path('synthetic-text.pdf'))
        ocr.assert_not_called()

    def test_ocr_unavailable_returns_none_without_raising(self):
        with patch.object(files, '_find_tesseract', return_value=''), \
             patch('pdfminer.high_level.extract_text', return_value=''), \
             patch.object(files, '_read_pdf_basic', return_value=None):
            self.assertIsNone(files._ocr_pdf(Path('missing-file.pdf')))


if __name__ == '__main__':
    unittest.main()