"""T5 lecture replay acceptance: media matrix + sampled ASR/transcript checks.

Rules implemented here (the source of truth is docs/TEST5_LECTURE_REPLAY.md):
  T5.1 every .mp4 decodes and has an audio stream
  T5.2 duration metadata is readable and positive
  T5.3 sampled 3-minute excerpts complete local ASR without crashing
  T5.4 transcribe mode persists a transcript and creates zero evidence clips
  T5.5 transcript timestamps are monotonic and duplicate rate is bounded
  T5.6 latency/RTF and key-term presence are reported (baseline, not gate yet)

The script does not upload media.  It runs the local engine in a subprocess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KEY_TERMS = ["方向导数", "偏导", "基底", "线性组合", "坐标", "多元函数",
             "开集", "邻域", "度量", "柯西", "不等式", "梯度", "连续"]


def media_info(path: Path, ffmpeg: str) -> dict:
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = result.stderr
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    audio = bool(re.search(r"Stream .*Audio:", text))
    video = bool(re.search(r"Stream .*Video:", text))
    return {"duration_s": duration, "audio": audio, "video": video,
            "decoded": bool(duration > 0 and audio and video)}


def excerpt(source: Path, out: Path, ffmpeg: str, start: float, seconds: float) -> bool:
    result = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(max(0.0, start)),
        "-t", str(seconds), "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(out),
    ], capture_output=True, timeout=180)
    return result.returncode == 0 and out.is_file() and out.stat().st_size > 44


def parse_transcript(path: Path) -> list[tuple[float, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\[(\d+):(\d+):(\d+)\]\s+([^:]+):\s+(.*)", line)
        if match:
            rows.append((int(match.group(1)) * 3600 + int(match.group(2)) * 60
                         + int(match.group(3)), match.group(5)))
    return rows


def duplicate_pairs(rows: list[tuple[float, str]], window: float = 4.0) -> int:
    pairs = 0
    for index, (ts, text) in enumerate(rows):
        for other_ts, other in rows[index + 1:index + 8]:
            if other_ts - ts > window:
                break
            if SequenceMatcher(None, text, other).ratio() >= 0.85:
                pairs += 1
    return pairs


def run_transcribe(engine_python: str, wav: Path, out: Path, log: Path) -> tuple[bool, float]:
    started = time.monotonic()
    with open(log, "w", encoding="utf-8") as stream:
        result = subprocess.run(
            [engine_python, "-m", "meetingvar", "transcribe", "--wav", str(wav),
             "--out", str(out), "--device", "auto"],
            cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT,
            timeout=900,
        )
    return result.returncode == 0 and out.is_file(), time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="directory containing lecture .mp4 files")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--engine-python", default=sys.executable)
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    root = Path(args.root)
    videos = sorted(root.glob("*.mp4"))
    matrix = []
    for video in videos:
        info = media_info(video, ffmpeg)
        matrix.append({"file": video.name, **info})
    decode_ok = sum(1 for row in matrix if row["decoded"])

    # Sample evenly across the corpus, including the shortest and longest.
    ordered = sorted(videos, key=lambda p: media_info(p, ffmpeg)["duration_s"])
    picks = []
    if ordered:
        indexes = [0, len(ordered) // 2, len(ordered) - 1]
        picks = [ordered[i] for i in dict.fromkeys(indexes)][:max(1, args.samples)]

    samples = []
    with tempfile.TemporaryDirectory(prefix="mv_t5_") as tmp:
        tmpdir = Path(tmp)
        for index, video in enumerate(picks):
            info = media_info(video, ffmpeg)
            start = max(0.0, info["duration_s"] * 0.35)
            wav = tmpdir / f"sample_{index}.wav"
            out = tmpdir / f"sample_{index}.txt"
            log = tmpdir / f"sample_{index}.log"
            extracted = excerpt(video, wav, ffmpeg, start, args.seconds)
            if not extracted:
                samples.append({"file": video.name, "ok": False, "error": "audio excerpt failed"})
                continue
            ok, elapsed = run_transcribe(args.engine_python, wav, out, log)
            rows = parse_transcript(out) if out.is_file() else []
            monotonic = all(rows[i][0] <= rows[i + 1][0] for i in range(len(rows) - 1))
            pairs = duplicate_pairs(rows)
            text = "\n".join(line for _, line in rows)
            hits = [term for term in KEY_TERMS if term in text]
            samples.append({
                "file": video.name, "ok": ok, "elapsed_s": round(elapsed, 2),
                "audio_seconds": args.seconds, "rtf": round(elapsed / args.seconds, 3),
                "lines": len(rows), "duplicate_pairs": pairs,
                "duplicate_rate": round(pairs / max(1, len(rows)), 3),
                "monotonic": monotonic,
                "clips": 0 if "片段 0 个" in log.read_text(encoding="utf-8", errors="replace") else None,
                "key_term_hits": hits, "key_term_count": len(hits),
            })

    metrics = {
        "videos": len(videos), "decoded": decode_ok,
        "audio_streams": sum(1 for row in matrix if row["audio"]),
        "samples": samples,
        "sample_success": sum(1 for row in samples if row.get("ok")),
        "sampled_minutes": round(sum(row.get("audio_seconds", 0) for row in samples) / 60, 2),
    }
    checks = {
        "T5.1_decode": decode_ok == len(videos) and bool(videos),
        "T5.2_audio": all(row["audio"] for row in matrix) and bool(videos),
        "T5.3_asr": metrics["sample_success"] == len(samples) and bool(samples),
        "T5.4_no_clips": all(row.get("clips") == 0 for row in samples),
        "T5.5_monotonic": all(row.get("monotonic") for row in samples),
        "T5.5_duplicate_rate": all(row.get("duplicate_rate", 1) < 0.10 for row in samples),
        "T5.6_rtf": all(row.get("rtf", 99) < 1.5 for row in samples),
    }
    report = {"root": str(root), "metrics": metrics, "checks": checks,
              "passed": all(checks.values()), "matrix": matrix}
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        lines = ["# T5 lecture replay acceptance", "",
                 f"Videos: {len(videos)} · decoded: {decode_ok} · samples: {len(samples)}", "",
                 "| Check | Result |", "|---|---|"]
        lines += [f"| {name} | {'PASS' if ok else 'FAIL'} |" for name, ok in checks.items()]
        lines += ["", "| Sample | RTF | Lines | Dup rate | Key terms |", "|---|---:|---:|---:|---:|"]
        for row in samples:
            lines.append(f"| {row.get('file', '')} | {row.get('rtf', '—')} | "
                         f"{row.get('lines', 0)} | {row.get('duplicate_rate', '—')} | "
                         f"{row.get('key_term_count', 0)} |")
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks,
                      "metrics": {k: v for k, v in metrics.items() if k != "samples"}},
                     ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())