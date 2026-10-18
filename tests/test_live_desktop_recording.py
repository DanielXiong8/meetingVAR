"""Captions and recording regressions; all input is synthetic, no devices or models."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "desktop")]
from meetingvar import config
from meetingvar.capture.screen import ScreenCapture
from meetingvar.capture.video import frame_segments, encode_clip
from meetingvar.pipeline import Session
from meetingvar.pool.store import Pool, pcm_to_wav_bytes
import app as desktop


class RecordingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mv_recording_test_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        settings = patch.multiple(config, DATA_DIR=self.root, POOL_DIR=self.root / "pool", CLIPS_DIR=self.root / "clips")
        settings.start()
        self.addCleanup(settings.stop)

    def screen(self, **kw):
        with patch.object(ScreenCapture, "_loop"), patch("threading.Event.wait", return_value=True):
            s = ScreenCapture(**kw)
        return s

    def backend(self):
        with patch.object(desktop, "load_config", return_value=dict(desktop.CONFIG_DEFAULTS)), \
             patch.object(desktop.Backend, "_autodetect"), \
             patch.object(desktop, "SESSMAP_FILE", self.root / "nonexistent.json"):
            b = desktop.Backend()
        b._cfg["repo_dir"] = str(self.root)
        return b

    def test_every_caption_survives_json_transport_without_truncation(self):
        text = "这是超过八十个字的完整字幕。" * 12 + "\n下一行 [INFO] 也必须保留"
        segs = [{"start": i, "end": i + 1, "speaker": "Alex Smith", "text": t}
                for i, t in enumerate(["第一句话", "第二句话", text])]
        session = Session("captions", [], device="cpu", echo=True)
        session.asr.with_embeddings = False
        output = io.StringIO()
        with patch.dict(os.environ, {"MEETINGVAR_DESKTOP_EVENTS": "1"}), \
             patch.object(session.asr, "transcribe_pcm16k", return_value=segs), \
             contextlib.redirect_stdout(output):
            session.ingest_pcm(bytes(8 * 32000), 1000)
        b = self.backend()
        for line in output.getvalue().splitlines():
            b._handle_line(line)
        captions = [e for e in b.poll_events()["events"] if e["type"] == "line"]
        self.assertEqual(len(captions), 3)
        self.assertEqual(captions[-1]["text"], text)
        self.assertEqual(captions[-1]["spk"], "Alex Smith")
        self.assertFalse((self.root / "pool").exists())

    def test_cli_echo_contains_every_full_caption(self):
        session = Session("cli", [], device="cpu", echo=True)
        text = "完整长句" * 40
        with patch.dict(os.environ, {"MEETINGVAR_DESKTOP_EVENTS": "0"}), \
             patch.object(session.asr, "transcribe_pcm16k", return_value=[{"start": 0, "end": 1, "text": text}]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            session.ingest_pcm(bytes(8 * 32000), 1000)
        self.assertIn(text, out.getvalue())

    def test_static_screen_is_deduplicated_and_retained(self):
        s = self.screen(retention_seconds=330)
        for ts in range(1000, 2000):
            s._store_frame(ts, b"same-jpeg")
        self.assertEqual(len(s.ring), 1)
        self.assertEqual(s.frames_since(1900), [(1000, b"same-jpeg")])
        self.assertEqual(s.get_status()["buffer_bytes"], len(b"same-jpeg"))

    def test_timed_buffer_covers_five_minutes_at_five_fps(self):
        s = self.screen()
        for i in range(2000):
            s._store_frame(1000 + i / 5, str(i).encode())
        frames = s.frames_since(1399.8 - 300)
        self.assertLessEqual(frames[0][0], 1099.8)
        self.assertGreater(len(frames), 1499)

    def test_memory_budget_is_hard_and_visible(self):
        s = self.screen(max_bytes=20)
        for ts in range(20):
            s._store_frame(ts, bytes([ts]) * 8)
        self.assertLessEqual(s.get_status()["buffer_bytes"], 20)
        self.assertTrue(s.get_status()["limited"])
        self.assertEqual(len(s.ring), 2)

    def test_frame_budget_compatibility_and_boundary_frame(self):
        s = self.screen(buffer_frames=2)
        for ts in [10, 20, 30]:
            s._store_frame(ts, bytes([ts]))
        self.assertEqual([t for t, _ in s.frames_since(25)], [20, 30])

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow required for image encoding")
    def test_encoder_preserves_aspect_and_caps_width(self):
        from PIL import Image
        jpg = ScreenCapture._encode(Image.new("RGB", (3200, 1800)))
        with Image.open(io.BytesIO(jpg)) as image:
            self.assertEqual(image.size, (1600, 900))

    def test_capture_exception_is_observable(self):
        s = self.screen()
        s._store_frame(10, b"frame")
        def broken():
            raise RuntimeError("capture lost")
        self.assertFalse(s._observe(broken))
        self.assertIn("capture lost", s.get_status()["error"])
        self.assertEqual(s.get_status()["last_success_ts"], 10)

    def test_frame_timeline_holds_preceding_frame_and_marks_missing_start(self):
        self.assertEqual(frame_segments([(9, "a"), (12, "b")], 10, 5), [("a", 2), ("b", 3)])
        self.assertEqual(frame_segments([(12, "b")], 10, 5), [("screens/unavailable.jpg", 2), ("b", 3)])

    def test_preview_is_not_accumulated_in_event_queue(self):
        b = self.backend()
        for i in range(20):
            b._handle_line('@@MEETINGVAR_EVENT ' + json.dumps({"type": "screen_state", "preview": str(i)}))
        self.assertEqual(len(b._events), 0)
        self.assertEqual(b.poll_events()["screen"]["preview"], "19")

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow required for synthetic screen frames")
    def test_library_returns_saved_frame_timeline(self):
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (160, 90), "red").save(output, format="JPEG")
        with patch.multiple(config, POOL_DIR=self.root / "data/pool", CLIPS_DIR=self.root / "data/clips"):
            pool = Pool("demo")
            pool.add_clip("L3", "test", pcm_to_wav_bytes(bytes(32000)), [],
                          screens=[(10, output.getvalue())], audio_start_ts=10, audio_end_ts=11)
        b = self.backend()
        clip = b.session_detail("demo")["clips"][0]
        self.assertEqual(clip["frames"][0]["ts"], 10)
        self.assertIn("/screens/frame_0000.jpg", clip["frames"][0]["url"])
        self.assertEqual(clip["start_ts"], 10)

    def test_close_waits_for_recording_finalization(self):
        b = self.backend()
        with patch.object(b, "is_running", return_value=True), patch.object(b, "stop_meeting") as stop:
            self.assertFalse(b._allow_close())
            self.assertEqual(b._capture_phase, "stopping")
            self.assertFalse(b._allow_close())
            stop.assert_called_once()
        with patch.object(b, "is_running", return_value=False):
            self.assertTrue(b._allow_close())

    def test_media_server_supports_seeking_without_full_download(self):
        from urllib.request import build_opener, ProxyHandler, Request
        from urllib.error import HTTPError
        b = self.backend()
        (self.root / "data").mkdir()
        (self.root / "data" / "synthetic.mp4").write_bytes(bytes(range(100)))
        b._ensure_server()
        self.addCleanup(b._server.server_close)
        self.addCleanup(b._server.shutdown)
        opener = build_opener(ProxyHandler({}))
        url = b._media_url(str(self.root / "data/synthetic.mp4"))
        for header, expected in [("bytes=10-19", bytes(range(10, 20))), ("bytes=-5", bytes(range(95, 100)))]:
            with opener.open(Request(url, headers={"Range": header}), timeout=3) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
        with self.assertRaises(HTTPError) as caught:
            opener.open(Request(url, headers={"Range": "bytes=100-"}), timeout=3)
        self.assertEqual(caught.exception.code, 416)
        caught.exception.close()

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow required for synthetic video")
    def test_ffmpeg_synthetic_clip_has_audio_and_timed_color_change(self):
        from PIL import Image
        from meetingvar.capture.hd import _ffmpeg
        import subprocess
        import wave
        ff = _ffmpeg()
        if not ff:
            self.skipTest("ffmpeg unavailable")
        frames = []
        for ts, color in [(0, "red"), (1, "blue")]:
            out = io.BytesIO()
            Image.new("RGB", (160, 90), color).save(out, format="JPEG")
            frames.append((ts, out.getvalue()))
        pool = Pool("synthetic")
        clip_id = pool.add_clip("L3", "synthetic", pcm_to_wav_bytes(bytes(64000)), [], screens=frames,
                                audio_start_ts=0, audio_end_ts=2)
        clip_path = pool.dir / (clip_id + ".json")
        video = encode_clip(clip_path, ff)
        self.assertTrue(video.exists())
        self.assertEqual(json.loads(clip_path.read_text(encoding="utf-8"))["screen"]["video_path"], video.as_posix())
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for ts, channel in [(0.4, 0), (1.4, 2)]:
            result = subprocess.run([ff, "-v", "error", "-ss", str(ts), "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=30, creationflags=flags)
            self.assertEqual(result.returncode, 0)
            pixel = result.stdout[:3]
            self.assertGreater(pixel[channel], 180)
            self.assertLess(pixel[2 - channel], 80)
        wav = self.root / "decoded.wav"
        result = subprocess.run([ff, "-v", "error", "-i", str(video), "-vn", "-acodec", "pcm_s16le", str(wav)], capture_output=True, timeout=30, creationflags=flags)
        self.assertEqual(result.returncode, 0)
        with wave.open(str(wav), "rb") as stream:
            self.assertAlmostEqual(stream.getnframes() / stream.getframerate(), 2, delta=0.1)


if __name__ == "__main__":
    unittest.main()
