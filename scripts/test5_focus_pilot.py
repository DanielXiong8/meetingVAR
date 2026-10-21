"""Run exam-driven focus questions against lecture excerpts.

Cases JSON:
[
  {"id":"F2","video":"...mp4","duration_s":9032,"start_ratio":0.06,
   "focus_dir":".../F2_chain_rule","keywords":["链式法则","复合"]}
]

For each case this extracts a 10-minute excerpt, runs `meetingvar replay`
with the focus documents, and reports whether saved clips contain the focus
keywords.  This is a pilot for precision, not a substitute for human topic
timestamps.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--seconds", type=float, default=600.0)
    parser.add_argument("--engine-python", default=sys.executable)
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    with tempfile.TemporaryDirectory(prefix="mv_t5_focus_") as tmp:
        for case in cases:
            source = Path(case["video"])
            wav = Path(tmp) / f"{case['id']}.wav"
            log = Path(tmp) / f"{case['id']}.log"
            start = float(case.get("start_ratio", 0.05)) * float(case["duration_s"])
            extracted = subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(max(0, start)),
                "-t", str(args.seconds), "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(wav),
            ], capture_output=True, timeout=180)
            if extracted.returncode or not wav.is_file():
                results.append({**case, "ok": False, "error": "excerpt extraction failed"})
                continue
            started = time.monotonic()
            with open(log, "w", encoding="utf-8") as stream:
                run = subprocess.run(
                    [args.engine_python, "-m", "meetingvar", "replay", "--wav", str(wav),
                     "--memory", str(case["focus_dir"]), "--device", "auto"],
                    cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT, timeout=900)
            elapsed = time.monotonic() - started
            log_text = log.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"--session (replay_\d+)", log_text)
            session = match.group(1) if match else ""
            clips = []
            if session:
                pool = ROOT / "data" / "pool" / session
                for path in sorted(pool.glob("*.json")) if pool.is_dir() else []:
                    if path.name in {"summary.json", "transcript.json"}:
                        continue
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    text = " ".join(str(line.get("text", "")) for line in payload.get("transcript", []))
                    keywords = [word for word in case.get("keywords", []) if word in text]
                    audio = payload.get("audio") or {}
                    duration = max(0.0, float(audio.get("end_ts", 0)) - float(audio.get("start_ts", 0)))
                    clips.append({"clip_id": path.stem, "saved_seconds": round(duration, 2),
                                  "layer": payload.get("trigger", {}).get("layer", ""),
                                  "evidence": payload.get("trigger", {}).get("evidence", ""),
                                  "lines": len(payload.get("transcript", [])),
                                  "keywords": keywords, "related": bool(keywords)})
            related = sum(1 for clip in clips if clip["related"])
            results.append({
                "id": case["id"], "question": case.get("question", ""),
                "video": source.name, "ok": run.returncode == 0 and bool(session),
                "elapsed_s": round(elapsed, 2), "session": session,
                "clips": len(clips), "related_clips": related,
                "clip_precision": (related / len(clips)) if clips else None,
                "saved_seconds": round(sum(c.get("saved_seconds", 0) for c in clips), 2),
                "saved_minutes_per_hour": round(sum(c.get("saved_seconds", 0) for c in clips) / 60 / (args.seconds / 3600), 2),
                "hit": related > 0, "details": clips,
                "tail": log_text[-500:] if run.returncode else "",
            })
    report = {"cases": results,
              "hits": sum(1 for c in results if c.get("hit")),
              "cases_total": len(results),
              "clip_precision_micro": (
                  sum(c.get("related_clips", 0) for c in results) /
                  max(1, sum(c.get("clips", 0) for c in results)))}
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        lines = ["# Test5 exam focus pilot", "",
                 "| Case | Clips | Related | Precision | Saved min/h | Hit |", "|---|---:|---:|---:|---:|---|"]
        for row in results:
            prec = "—" if row.get("clip_precision") is None else f"{row['clip_precision']:.0%}"
            lines.append(f"| {row['id']} | {row.get('clips', 0)} | {row.get('related_clips', 0)} | "
                         f"{prec} | {row.get('saved_minutes_per_hour', 0):.1f} | "
                         f"{'YES' if row.get('hit') else 'NO'} |")
        lines += ["", f"Micro clip precision: {report['clip_precision_micro']:.1%}"]
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"hits": report["hits"], "cases": len(results),
                      "clip_precision": report["clip_precision_micro"]}, ensure_ascii=False))
    return 0 if report["hits"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())