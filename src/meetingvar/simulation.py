"""Deterministic end-to-end sandbox. Never used implicitly for real meetings.

Only the FunASR model and screen source are simulated. Audio conversion, ASR
adapter, correction, TF-IDF, Session, pool, reports, review and project memory
are production code. Generated WAVs contain encoded test signals, NOT speech.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from . import config

SCRIPT = [
    (2, "S1", "今天讨论测试污染，S3 需要重测。"),
    (8, "S2", "李明负责会后确认 S3 测序污染结果。"),
    (14, "S1", "UMI 评估和测序污染排查需要一起完成。"),
    (20, "S2", "李明明天提交 UMI 重测结果，继续排查测序污染。"),
]
DURATION = 24


def write_fixture(path: Path, channels: int = 2, rate: int = 16000) -> None:
    """A second's constant sample value encodes its absolute fixture offset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        for second in range(DURATION):
            stream.writeframes(array("h", [1000 + second] * rate * channels).tobytes())


class FixtureModel:
    """FunASR generate contract; validates its input rather than ignoring PCM."""
    def __init__(self):
        self.windows: list[dict] = []

    def generate(self, input: str, **kwargs) -> list[dict]:
        with wave.open(input, "rb") as stream:
            assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, 16000)
            raw = array("h", stream.readframes(stream.getnframes()))
        if not raw:
            return []
        offset = raw[0] - 1000
        duration = len(raw) / config.ASR_RATE
        self.windows.append({"offset": offset, "duration": duration})
        sentences = [
            {"start": (second - offset) * 1000, "end": (second + 1 - offset) * 1000,
             "spk": speaker, "text": f"<|zh|>{text}"}
            for second, speaker, text in SCRIPT
            if offset <= second and second + 1 <= offset + duration
        ]
        return [{"sentence_info": sentences}]


class FixtureScreen:
    def __init__(self, session):
        from PIL import Image
        from .capture.screen import ScreenCapture

        self.session = session
        self.jpg = ScreenCapture._encode(Image.new("RGB", (320, 180), (24, 54, 78)))

    def frames_since(self, since_ts):
        start = self.session._timeline_start_ts
        end = self.session._last_flush_ts
        if start is None or end is None:
            return []
        return [(start + second, self.jpg) for second in range(DURATION)
                if since_ts <= start + second <= end]


@contextlib.contextmanager
def _capture_log(log, root):
    try:
        with contextlib.redirect_stdout(log):
            yield
    finally:
        (root / "simulation.log").write_text(log.getvalue(), encoding="utf-8")


def run(output: Path) -> dict:
    """Keep artifacts for inspection; each run uses a new child directory."""
    from PIL import Image
    from . import cli
    from .pipeline import Session
    from .transcribe.asr import ASR
    from . import replay as replay_module
    from .memory.calibration import apply_reviews, load_threshold
    from .memory.project_store import archive_report
    from .pool.store import Pool

    root = output.resolve() / f"run_{time.time_ns()}"
    root.mkdir(parents=True, exist_ok=False)
    data = root / "data"
    project = root / "project"
    project.mkdir()
    (project / "notes.md").write_text(
        "# 测序污染\n- S3：需要重测。\n- UMI：评估指标。\n"
        "李明负责 S3 测序污染排查和 UMI 评估。李明负责会后确认结果。\n", encoding="utf-8")
    wav = root / "fixture_stereo.wav"
    write_fixture(wav)
    model = FixtureModel()
    sessions = []

    def load_fixture(asr):
        asr._model = model

    def make_session(*args, **kwargs):
        session = Session(*args, **kwargs)
        session.screen_capture = FixtureScreen(session)
        session.manual.fire()
        sessions.append(session)
        return session

    def command(*args):
        with patch.object(sys, "argv", ["meetingvar", *map(str, args)]):
            cli.main()

    log = io.StringIO()
    # Patch settings only inside the sandbox; never touch the user's data.
    with _capture_log(log, root), contextlib.ExitStack() as stack:
        for name, value in {
            "DATA_DIR": data, "POOL_DIR": data / "pool", "CLIPS_DIR": data / "clips",
            "TERMS_FILE": data / "terms.txt", "CHUNK_SECONDS": 8,
            "CHUNK_OVERLAP_SECONDS": 3, "L2_SIM_THRESHOLD": 0.25,
            "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": "",
        }.items():
            stack.enter_context(patch.object(config, name, value))
        stack.enter_context(patch.object(ASR, "_ensure_loaded", load_fixture))
        stack.enter_context(patch.object(replay_module, "Session", make_session))
        command("terms", "--memory", project, "--out", config.TERMS_FILE)
        command("replay", "--wav", wav, "--terms", config.TERMS_FILE, "--memory", project, "--device", "cpu")
        session = sessions[0]
        pool = Pool(session.session_id)
        transcript = json.loads((pool.dir / "transcript.json").read_text(encoding="utf-8"))["transcript"]
        assert len(transcript) == len(SCRIPT), transcript
        assert [line["ts"] for line in transcript] == [float(x[0]) for x in SCRIPT]
        assert "测序污染" in transcript[0]["text"] and "测试污染" not in transcript[0]["text"], transcript[0]
        clips = pool.list_clips()
        layers = {clip["trigger"]["layer"] for clip in clips}
        assert layers == {"L1", "L2", "L3"}, layers
        frame_count = 0
        for clip in clips:
            audio = clip["audio"]
            with wave.open(audio["path"], "rb") as stream:
                duration = stream.getnframes() / stream.getframerate()
                assert stream.getnchannels() == 1
            assert abs(duration - (audio["end_ts"] - audio["start_ts"])) < 1 / 16000
            assert 0 <= audio["start_ts"] <= audio["end_ts"] <= DURATION
            for ref in clip["screen"]["keyframes"]:
                with Image.open(config.CLIPS_DIR / ref) as image:
                    image.verify()
                frame_count += 1
        assert frame_count > 0
        command("digest", "--session", session.session_id)
        minutes = (data / "reports" / session.session_id / "minutes.md").read_text(encoding="utf-8")
        assert "明确行动项" in minutes and "〔模板版〕" in minutes
        assert "@S3" not in minutes and "@UMI" not in minutes
        chosen = next(clip for clip in clips if clip["trigger"]["layer"] == "L2")
        command("review", "--session", session.session_id, "--clip", chosen["clip_id"][:8],
                "--status", "useless", "--note", "仿真显式评价，用于验证校准回写", "--project", project)
        threshold = load_threshold(project)
        assert threshold > 0.25
        assert apply_reviews(project, pool.list_clips())["new_samples"] == 0
        archive = archive_report(project, session.session_id)
        command("rescan", project)
        # Real replay CLI loads the persisted project calibration in session #2.
        # Give it a distinct ID without waiting or changing the system clock.
        with patch.object(cli.time, "time", return_value=time.time() + 10):
            command("replay", "--wav", wav, "--terms", config.TERMS_FILE, "--memory", project, "--device", "cpu")
        assert sessions[1].scorer.threshold == threshold
        # Privacy control: no trigger means no audio or screen persistence.
        silent = Session("no_trigger", terms=[], echo=False, device="cpu")
        with patch.object(silent.asr, "transcribe_pcm16k", return_value=[]):
            silent.ingest_pcm(bytes(16000 * 2 * 8), 1000.0)
            assert silent.finish()["clips"] == 0
        assert not Pool("no_trigger").list_clips()
        assert not (config.CLIPS_DIR / "no_trigger").exists()
        result = {
            "status": "passed", "mode": "simulation_not_real_speech",
            "python": sys.version.split()[0], "output": str(root),
            "session_id": session.session_id, "input_duration_seconds": DURATION,
            "transcript_lines": len(transcript), "clips": len(clips),
            "layers": sorted(layers), "verified_jpeg_files": frame_count,
            "asr_windows": model.windows, "next_session_l2_threshold": threshold,
            "archive": str(archive),
            "simulated": ["FunASR model.generate", "screen source", "human review"],
            "not_verified": ["real speech recognition accuracy", "WASAPI loopback", "GPU", "real LLM quality"],
        }
    (root / "simulation.log").write_text(log.getvalue(), encoding="utf-8")
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(log.getvalue())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/simulation"))
    run(parser.parse_args().output)
