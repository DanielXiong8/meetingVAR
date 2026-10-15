"""Real pipeline regressions; install requirements-simulation.txt to run."""
import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
AVAILABLE = sys.version_info[:2] == (3, 10) and all(
    importlib.util.find_spec(name) for name in ("jieba", "sklearn", "pypinyin", "PIL")
)


@unittest.skipUnless(AVAILABLE, "Python 3.10 + requirements-simulation.txt required")
class PipelineSimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from meetingvar import config
        self.settings = patch.multiple(config, DATA_DIR=self.root, POOL_DIR=self.root / "pool",
                                       CLIPS_DIR=self.root / "clips")
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.tmp.cleanup()

    def test_stereo_downmix_and_resample(self):
        from meetingvar.replay import wav_to_pcm16k
        wav = self.root / "stereo.wav"
        with wave.open(str(wav), "wb") as stream:
            stream.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(array("h", [1000, 3000] * 48000).tobytes())
        pcm = wav_to_pcm16k(wav)
        self.assertEqual(len(pcm), 32000)
        self.assertEqual(set(array("h", pcm)), {2000})

    def test_streaming_capture_poll_does_not_drop_frames(self):
        import threading
        from collections import deque
        from meetingvar.capture.audio import AudioCapture

        capture = AudioCapture.__new__(AudioCapture)
        capture._lock = threading.Lock()
        capture._ring = deque()
        cursor = 1000.0
        total = b""
        for i in range(10):
            chunk = array("h", [i] * 1600).tobytes()
            capture._ring.append((1000.0 + i * 0.1, chunk))
            pcm, start = capture.ingest_since(cursor)
            total += pcm
            cursor = start + len(pcm) / 32000
        self.assertEqual(len(total), 32000)
        self.assertEqual(array("h", total)[::1600].tolist(), list(range(10)))

    def test_streaming_stereo_conversion_matches_whole_wav(self):
        from meetingvar.capture.audio import to_pcm16k

        raw = array("h", [1000, 3000] * 48000).tobytes()
        whole, _ = to_pcm16k(raw, 2, 48000)
        state, chunks = None, []
        for offset in range(0, len(raw), 19200):
            pcm, state = to_pcm16k(raw[offset:offset + 19200], 2, 48000, state)
            chunks.append(pcm)
        self.assertEqual(b"".join(chunks), whole)

    def test_ring_lookback_trims_audio_and_screen(self):
        from meetingvar import pipeline
        from meetingvar.pool.store import Pool
        screen = Mock()
        screen.frames_since.return_value = [(1009.0, b"jpeg"), (1011.0, b"future")]
        session = pipeline.Session("trim", terms=[], echo=False, device="cpu", screen_capture=screen)
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[]), patch.object(pipeline, "LOOKBACK_SECONDS", 3):
            session.ingest_pcm(bytes(10 * 32000), 1000.0)
            clip_id = session.trigger_manual()
        clip = Pool("trim").list_clips()[0]
        self.assertEqual(clip["clip_id"], clip_id)
        self.assertEqual((clip["audio"]["start_ts"], clip["audio"]["end_ts"]), (7.0, 10.0))
        self.assertEqual(len(clip["screen"]["keyframes"]), 1)
        with wave.open(clip["audio"]["path"], "rb") as stream:
            self.assertEqual(stream.getnframes(), 3 * 16000)

    def test_overlap_tail_deduplicates_sentences_and_finish(self):
        from meetingvar.pipeline import Session
        session = Session("overlap", terms=[], echo=False, device="cpu")
        responses = [
            [{"start": 8, "end": 9, "speaker": "S1", "text": "重复句"}],
            [{"start": 1, "end": 2, "speaker": "S1", "text": "重复句"},
             {"start": 7, "end": 8, "speaker": "S1", "text": "重复句"}],
        ]
        with patch.object(session.asr, "transcribe_pcm16k", side_effect=responses) as asr:
            session.ingest_pcm(bytes(10 * 32000), 1000.0)
            self.assertEqual(len(session._pending[0][1]), 3 * 32000)
            session.ingest_pcm(bytes(5 * 32000), 1010.0)
            first = session.finish()
            second = session.finish()
        self.assertEqual(asr.call_count, 2)
        self.assertEqual(first["lines"], 2)
        self.assertEqual(first, second)

    def test_terms_are_not_person_assignees(self):
        from meetingvar import config
        from meetingvar.report.generate import _thin_no_llm
        (config.DATA_DIR / "persons.json").write_text('[{"name":"李明"}]', encoding="utf-8")
        minutes = _thin_no_llm([{"ts": 0, "speaker": "S1", "text": "李明负责 S3 和 UMI 测试"}], {"S3": 1, "UMI": 1})
        self.assertIn("@李明", minutes)
        self.assertNotIn("@S3", minutes)
        self.assertNotIn("/S3", minutes)

    def test_full_cli_workflow(self):
        from meetingvar.simulation import run
        with contextlib.redirect_stdout(io.StringIO()):
            result = run(self.root / "runs")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["transcript_lines"], 4)
        self.assertEqual(result["layers"], ["L1", "L2", "L3"])
        self.assertTrue((Path(result["output"]) / "result.json").exists())

    def test_asr_passes_waveform_without_temporary_media(self):
        from meetingvar.transcribe.asr import ASR
        import numpy as np

        asr = ASR("cpu")
        with patch.object(asr, "_ensure_loaded"), patch.object(asr, "_model") as model, \
             patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("draft media written")):
            model.generate.return_value = []
            self.assertEqual(asr.transcribe_pcm16k(array("h", [-32768, 0, 32767]).tobytes()), [])
            waveform = model.generate.call_args.kwargs["input"]
            np.testing.assert_allclose(waveform, [-1.0, 0.0, 32767 / 32768])
            self.assertEqual(waveform.dtype, np.float32)

    def test_no_trigger_keeps_draft_text_only_in_memory(self):
        from meetingvar.pipeline import Session

        session = Session("no_hit_text", terms=[], device="cpu", echo=False)
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[
            {"start": 1, "end": 2, "speaker": "S1", "text": "这是一句没有监听词的发言"}
        ]):
            session.ingest_pcm(bytes(8 * 32000), 1000.0)
            summary = session.finish()
        self.assertEqual(summary["clips"], 0)
        self.assertEqual(summary["lines"], 1)
        self.assertIsNone(summary["transcript_json"])
        self.assertFalse((self.root / "pool").exists())
        self.assertFalse((self.root / "clips").exists())

    def test_silent_session_creates_no_files_and_stops_hd(self):
        from meetingvar.pipeline import Session

        session = Session("silent", terms=[], device="cpu", echo=False)
        session.hd = Mock()
        before = {p for p in self.root.rglob("*") if p.is_file()}
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[]):
            session.ingest_pcm(bytes(8 * 32000), 1000.0)
            self.assertIsNone(session.finish()["transcript_json"])
        session.hd.stop.assert_called_once()
        self.assertEqual(before, {p for p in self.root.rglob("*") if p.is_file()})

    def test_full_300_second_lookback(self):
        from meetingvar.pipeline import Session
        from meetingvar.pool.store import Pool

        session = Session("long", terms=[], device="cpu", echo=False)
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[]):
            session.ingest_pcm(array("h", [1000] * 350 * 16000).tobytes(), 1000.0)
        session.trigger_manual()
        clip = Pool("long").list_clips()[0]
        self.assertEqual((clip["audio"]["start_ts"], clip["audio"]["end_ts"]), (50.0, 350.0))
        with wave.open(clip["audio"]["path"], "rb") as stream:
            self.assertEqual(stream.getnframes() / stream.getframerate(), 300)
            self.assertEqual(array("h", stream.readframes(1))[0], 1000)

    def test_empty_interest_vocabulary_is_safe(self):
        from meetingvar.monitor.hotwords import InterestScorer

        self.assertEqual(InterestScorer(["a ， !"]).check("没有可用词表"), (False, 0.0))

    def test_go_releases_capture_if_finish_fails(self):
        from meetingvar import cli, pipeline
        from meetingvar.capture import audio, screen

        project = self.root / "project"
        project.mkdir()
        with patch.object(audio, "AudioCapture") as capture, \
             patch.object(screen, "ScreenCapture") as screenshots, \
             patch.object(pipeline, "Session") as session, \
             patch("builtins.input", side_effect=EOFError):
            capture.return_value.ingest_since.side_effect = KeyboardInterrupt
            session.return_value.finish.side_effect = RuntimeError("simulated ASR failure")
            with patch.object(sys, "argv", ["meetingvar", "go", str(project), "--no-repo", "--source", "loopback"]):
                with self.assertRaisesRegex(RuntimeError, "simulated ASR failure"):
                    cli.main()
            capture.return_value.stop.assert_called_once()
            screenshots.return_value.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
