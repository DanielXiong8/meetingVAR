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
from unittest.mock import patch

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

    def test_ring_lookback_trims_audio_and_screen(self):
        from meetingvar import pipeline
        from meetingvar.pool.store import Pool
        session = pipeline.Session("trim", terms=[], echo=False, device="cpu")
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[]), patch.object(pipeline, "LOOKBACK_SECONDS", 3):
            session.ingest_pcm(bytes(10 * 32000), 1000.0)
            clip_id = session.trigger_manual()
        clip = Pool("trim").list_clips()[0]
        self.assertEqual(clip["clip_id"], clip_id)
        self.assertEqual((clip["audio"]["start_ts"], clip["audio"]["end_ts"]), (7.0, 10.0))
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


if __name__ == "__main__":
    unittest.main()
