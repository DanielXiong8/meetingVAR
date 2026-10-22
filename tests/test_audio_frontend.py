"""Audio front-end: processed WAV is clean 16 kHz mono, source stays untouched."""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.transcribe.frontend import _ffmpeg, preprocess_wav  # noqa: E402


def _write_tone(path: Path, seconds: float = 0.5, rate: int = 44100) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            sample = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<hh", sample, sample)
        out.writeframes(bytes(frames))


@unittest.skipIf(_ffmpeg() is None, "ffmpeg unavailable")
class AudioFrontendTests(unittest.TestCase):
    def test_preprocess_produces_16k_mono_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "raw.wav"
            target = Path(tmp) / "clean.wav"
            _write_tone(source)
            before = source.read_bytes()
            self.assertTrue(preprocess_wav(source, target, denoise=True, normalize=True))
            self.assertEqual(source.read_bytes(), before)
            with wave.open(str(target), "rb") as wav:
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getnchannels(), 1)

    def test_preprocess_reports_failure_for_non_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notes.txt"
            target = Path(tmp) / "clean.wav"
            source.write_text("not audio", encoding="utf-8")
            self.assertFalse(preprocess_wav(source, target))


if __name__ == "__main__":
    unittest.main()