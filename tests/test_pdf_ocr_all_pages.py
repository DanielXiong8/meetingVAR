"""All-pages OCR default, env override and resume behavior (no real PDF needed)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.memory import files


class _Pixmap:
    def save(self, target):
        Path(target).write_bytes(b'png')


class _Page:
    def get_pixmap(self, **kwargs):
        return _Pixmap()


class _Document:
    def __init__(self, pages):
        self.pages = pages

    def __iter__(self):
        return iter([_Page() for _ in range(self.pages)])

    def __len__(self):
        return self.pages

    def __getitem__(self, index):
        if not 0 <= index < self.pages:
            raise IndexError(index)
        return _Page()

    def close(self):
        pass


class OcrAllPagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mv-ocr-pages-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'scan.pdf'
        self.pdf.write_bytes(b'%PDF-1.4 synthetic')

    def run_ocr(self, pages, calls, max_pages=None, env=None, part=None):
        fake_fitz = types.SimpleNamespace(open=lambda path: _Document(pages), csRGB=0)
        counter = {'n': 0}

        def fake_run(*args, **kwargs):
            counter['n'] += 1
            return types.SimpleNamespace(returncode=0, stdout='第 一 页 文 字 内 容')

        def cache_dir():
            return self.root

        with patch.object(files, '_find_tesseract', return_value='tesseract'), \
             patch.object(files, '_cache_dir', side_effect=cache_dir), \
             patch.object(files, '_cache_key', return_value='key'), \
             patch.dict(sys.modules, {'fitz': fake_fitz}), \
             patch.object(files.subprocess, 'run', side_effect=fake_run), \
             patch.dict(os.environ, env or {}, clear=False):
            if env is not None:
                for key in ('MEETINGVAR_OCR_MAX_PAGES',):
                    if key not in env:
                        os.environ.pop(key, None)
            if part:
                (self.root / 'key-ocr.part.txt').write_text(part[0], encoding='utf-8')
                (self.root / 'key-ocr.part.json').write_text(
                    json.dumps({'pages_done': part[1]}), encoding='utf-8')
            text = files._ocr_pdf(self.pdf, max_pages=max_pages)
        self.assertEqual(counter['n'], calls)
        self.assertTrue(text)

    def test_default_scans_every_page(self):
        self.run_ocr(45, 45)

    def test_env_override_limits_pages(self):
        self.run_ocr(45, 5, env={'MEETINGVAR_OCR_MAX_PAGES': '5'})

    def test_explicit_limit_still_supported(self):
        self.run_ocr(45, 3, max_pages=3)

    def test_legacy_resume_without_page_coverage_is_not_trusted(self):
        self.run_ocr(20, 20, part=('已 扫 描 前 十 页', 10))


if __name__ == '__main__':
    unittest.main()