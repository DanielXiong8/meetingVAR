"""Clean-Windows contracts: no preinstalled office/PDF/media application.

These tests assert that optional capabilities come from Python wheels or the
application's own runtime directory, never from a host application such as
LibreOffice, pdftotext or a system ffmpeg.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class StandaloneRuntimeTests(unittest.TestCase):
    def test_ffmpeg_is_available_from_python_dependency(self):
        from meetingvar.capture.hd import _ffmpeg

        executable = _ffmpeg()
        self.assertTrue(executable, "应通过 imageio-ffmpeg 或应用自带目录找到 ffmpeg")
        result = subprocess.run([executable, "-version"], capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr[-300:])
        self.assertIn("ffmpeg version", result.stdout.decode("utf-8", "replace"))

    def test_asr_auto_device_falls_back_to_cpu_without_cuda(self):
        from unittest.mock import patch

        from meetingvar.transcribe.asr import ASR

        with patch("torch.cuda.is_available", return_value=False):
            self.assertEqual(ASR("auto")._device, "cpu")
            self.assertEqual(ASR("cuda")._device, "cpu")

    def test_legacy_office_parsers_do_not_use_host_apps(self):
        from meetingvar.memory.files import read_text_safely

        source = (ROOT / "src" / "meetingvar" / "memory" / "files.py").read_text(encoding="utf-8")
        self.assertNotIn("soffice", source.lower())
        self.assertNotIn("pdftotext", source.lower())
        fixtures = ROOT / "tests" / "fixtures" / "legacy"
        doc = read_text_safely(fixtures / "sample.doc")
        ppt = read_text_safely(fixtures / "sample.ppt")
        xls = read_text_safely(fixtures / "sample.xls")
        self.assertIn("Legacy Word 正文测试", doc or "")
        self.assertIn("Legacy PPT 标题", ppt or "")
        self.assertIn("负责人", xls or "")


if __name__ == "__main__":
    unittest.main()