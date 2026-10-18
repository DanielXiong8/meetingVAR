"""Failure-path, startup-buffer and manual-trigger regressions without devices."""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _fakes import FakeASR
from meetingvar import config
from meetingvar.live import record_session


class LiveReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = patch.multiple(config, DATA_DIR=self.root, POOL_DIR=self.root / "pool",
                                       CLIPS_DIR=self.root / "clips")
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.args = argparse.Namespace(no_screen=False, hd=False, device="cpu", duration=0,
                                       audio_device=None)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_screen_initialization_failure_releases_audio(self):
        with patch("meetingvar.capture.audio.AudioCapture") as capture, \
             patch("meetingvar.capture.screen.ScreenCapture", side_effect=RuntimeError("screen failed")):
            with self.assertRaisesRegex(RuntimeError, "screen failed"):
                record_session(self.args, "fail_screen", [], None, None)
            capture.return_value.stop.assert_called_once()

    def test_session_initialization_failure_releases_all_devices(self):
        self.args.hd = True
        with patch("meetingvar.capture.audio.AudioCapture") as capture, \
             patch("meetingvar.capture.screen.ScreenCapture") as screen, \
             patch("meetingvar.capture.hd.HDCapture") as hd, \
             patch("meetingvar.pipeline.Session", side_effect=RuntimeError("session failed")):
            with self.assertRaisesRegex(RuntimeError, "session failed"):
                record_session(self.args, "fail_session", [], None, None)
            capture.return_value.stop.assert_called_once()
            screen.return_value.stop.assert_called_once()
            hd.return_value.stop.assert_called_once()

    def test_manual_during_no_new_audio_uses_existing_buffer(self):
        from meetingvar.pipeline import Session
        from meetingvar.pool.store import Pool
        session = Session("manual_silence", [], echo=False, device="cpu")
        session.asr = FakeASR()
        capture = Mock(session_start=1000.0, name="virtual")
        capture.name = "virtual"
        calls = []

        def poll(cursor):
            calls.append(cursor)
            if len(calls) == 1:
                return bytes(32000), 1000.0
            if len(calls) == 2:
                session.manual.fire()
                return b"", 1001.0
            self.assertEqual(len(session.clips), 1, "manual must save before finish")
            raise KeyboardInterrupt

        capture.ingest_since.side_effect = poll
        self.args.no_screen = True
        with patch("meetingvar.capture.audio.AudioCapture", return_value=capture), \
             patch("meetingvar.pipeline.Session", return_value=session), \
             patch("builtins.input", side_effect=EOFError), \
             patch("threading.Event.wait", return_value=False):
            summary = record_session(self.args, session.session_id, [], None, None)
        self.assertLessEqual(calls[0], capture.session_start)
        self.assertEqual(summary["clips"], 1)
        clip = Pool(session.session_id).list_clips()[0]
        with wave.open(clip["audio"]["path"], "rb") as wav:
            self.assertEqual(wav.getnframes(), 16000)
        capture.stop.assert_called_once()

    def test_explicit_device_is_forwarded(self):
        self.args.no_screen = True
        self.args.audio_device = 7
        with patch("meetingvar.capture.audio.AudioCapture") as capture, \
             patch("meetingvar.pipeline.Session") as session, \
             patch("builtins.input", side_effect=EOFError):
            capture.return_value.ingest_since.side_effect = KeyboardInterrupt
            session.return_value.manual.consume.return_value = False
            record_session(self.args, "device", [], None, None)
            capture.assert_called_once_with(device_index=7)

    def test_asr_failure_is_preserved_without_refeeding_buffer(self):
        from meetingvar.pipeline import Session
        session = Session("asr_failure", [], echo=False, device="cpu")
        session.asr = Mock()
        session.asr.transcribe_pcm16k.side_effect = RuntimeError("original ASR failure")
        capture = Mock(session_start=1000.0)
        capture.name = "virtual"
        capture.ingest_since.return_value = (bytes(8 * 32000), 1000.0)
        self.args.no_screen = True
        with patch("meetingvar.capture.audio.AudioCapture", return_value=capture), \
             patch("meetingvar.pipeline.Session", return_value=session), \
             patch.object(session, "finish") as finish, \
             patch("builtins.input", side_effect=EOFError):
            with self.assertRaisesRegex(RuntimeError, "original ASR failure"):
                record_session(self.args, session.session_id, [], None, None)
        capture.ingest_since.assert_called_once()
        capture.stop.assert_called_once()
        finish.assert_not_called()

    def test_shutdown_drains_audio_after_last_poll(self):
        from meetingvar.pipeline import Session
        from meetingvar.pool.store import Pool
        session = Session("final_tail", [], echo=False, device="cpu")
        session.asr = FakeASR()
        capture = Mock(session_start=1000.0)
        capture.name = "virtual"
        capture.ingest_since.side_effect = [
            (bytes(32000), 1000.0), KeyboardInterrupt, (bytes(8000), 1001.0),
        ]
        self.args.no_screen = True
        with patch("meetingvar.capture.audio.AudioCapture", return_value=capture), \
             patch("meetingvar.pipeline.Session", return_value=session), \
             patch("builtins.input", side_effect=EOFError):
            record_session(self.args, session.session_id, [], None, None)
        self.assertEqual(session._last_flush_ts, 1001.25)
        capture.stop.assert_called_once()

    def test_conflicting_screen_options_fail_before_opening_devices(self):
        self.args.no_screen = self.args.hd = True
        with patch("meetingvar.capture.audio.AudioCapture") as capture:
            with self.assertRaises(SystemExit):
                record_session(self.args, "screen_options", [], None, None)
            capture.assert_not_called()

    def test_default_endpoint_is_system_default_without_probe_streams(self):
        from meetingvar.capture.audio import pick_live_endpoint
        pa = Mock()
        pa.get_default_wasapi_loopback.return_value = {"index": 9}
        self.assertEqual(pick_live_endpoint(pa), {"index": 9})
        pa.open.assert_not_called()

    def test_audio_open_failure_terminates_portaudio(self):
        from meetingvar.capture.audio import AudioCapture
        pa = Mock()
        pa.get_default_wasapi_loopback.return_value = {
            "index": 9, "defaultSampleRate": 48000, "maxInputChannels": 2,
        }
        pa.open.side_effect = OSError("cannot open stream")
        module = types.SimpleNamespace(PyAudio=lambda: pa, paInt16=8)
        with patch.dict(sys.modules, {"pyaudiowpatch": module}):
            with self.assertRaisesRegex(OSError, "cannot open"):
                AudioCapture()
        pa.terminate.assert_called_once()

    def test_screen_backend_failure_reaches_caller(self):
        from meetingvar.capture.screen import ScreenCapture
        with patch.object(ScreenCapture, "_make_grabber", side_effect=RuntimeError("no backend")):
            with self.assertRaisesRegex(RuntimeError, "录屏初始化失败"):
                ScreenCapture()

    def test_manual_terms_survive_repeated_rescan(self):
        from meetingvar.cli import load_terms
        from meetingvar.memory.project_store import rescan_terms
        project = self.root / "project"
        project.mkdir()
        rescan_terms(project, extra_terms=["绝不丢失词"])
        rescan_terms(project)
        rescan_terms(project)
        terms = project / "meetingvar" / "terms.txt"
        self.assertIn("#!keep 绝不丢失词", terms.read_text(encoding="utf-8"))
        self.assertIn("绝不丢失词", load_terms(terms))

    def test_empty_keep_marker_is_safe(self):
        from meetingvar.cli import load_terms
        path = self.root / "terms.txt"
        path.write_text("#!keep\n#!keep 保留词\n", encoding="utf-8")
        self.assertEqual(load_terms(path), ["保留词"])

    def test_archive_cli_updates_existing_project_copy(self):
        from meetingvar.cli import main
        report = self.root / "reports" / "saved"
        report.mkdir(parents=True)
        minutes = report / "minutes.md"
        minutes.write_text("模板", encoding="utf-8")
        project = self.root / "project"
        argv = ["meetingvar", "archive", "--session", "saved", "--project", str(project)]
        with patch.object(sys, "argv", argv):
            main()
            minutes.write_text("更新后的纪要", encoding="utf-8")
            main()
        archived = project / "meetingvar" / "minutes" / "saved" / "minutes.md"
        self.assertEqual(archived.read_text(encoding="utf-8"), "更新后的纪要")


if __name__ == "__main__":
    unittest.main()
