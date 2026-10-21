"""Compare L1 hotword, L2 focus-document and L3 LLM judge on exam-driven clips.

Each case is a short lecture excerpt selected around an exam topic.  The script
transcribes it once, then applies the three methods to the same transcript.
L3 uses the configured OpenAI-compatible endpoint (local llama-server is fine).
Results are a pilot: excerpts are topic-selected, not human timestamp labels.
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


def parse_lines(path: Path) -> list[dict]:
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        match = re.match(r"\[(\d+):(\d+):(\d+)\]\s+([^:]+):\s+(.*)", line)
        if not match:
            continue
        ts = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
        rows.append({"id": f"turn_{index}", "ts": float(ts), "end_ts": float(ts + 2),
                     "speaker": match.group(4), "text": match.group(5)})
    for index, row in enumerate(rows[:-1]):
        row["end_ts"] = max(row["ts"] + 0.5, rows[index + 1]["ts"])
    return rows


def run_transcribe(engine_python: str, wav: Path, out: Path, log: Path) -> tuple[bool, float]:
    started = time.monotonic()
    with open(log, "w", encoding="utf-8") as stream:
        run = subprocess.run([engine_python, "-m", "meetingvar", "transcribe",
                              "--wav", str(wav), "--out", str(out), "--device", "auto"],
                             cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT,
                             timeout=900)
    return run.returncode == 0 and out.is_file(), time.monotonic() - started


def l3_judge(chat, question: str, rows: list[dict], timeout: int = 180) -> dict:
    from meetingvar.llm import chat_result

    transcript = "\n".join(f"[{row['id']}] {row['text']}" for row in rows)
    prompt = (
        "你是课堂内容裁判。判断下面这段课堂转写是否在实质讲解或回答关注问题。\n"
        "只有出现定义、定理、推导、例题或明确解释才算命中；只出现关键词、课程安排、"
        "闲聊、泛泛提到不算。转写来自 ASR，可能有错别字，允许根据上下文判断。\n"
        "只输出一个 JSON：{\"hit\":true/false,\"reason\":\"简短理由\",\"evidence_ids\":[\"turn_1\"]}；"
        "没有命中时 evidence_ids 为空数组。\n\n"
        f"关注问题：{question}\n转写：\n{transcript[:2500]}"
    )
    result = chat_result(prompt, temperature=0, timeout=timeout)
    if not result.get("ok"):
        return {"hit": False, "error": result.get("error_code") or "LLM unavailable",
                "reason": ""}
    raw = result.get("content", "")
    try:
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group()) if match else None
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("hit"), bool):
        return {"hit": False, "error": "invalid JSON", "reason": ""}
    return {"hit": data["hit"], "reason": str(data.get("reason", ""))[:300],
            "evidence_ids": data.get("evidence_ids") or []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--engine-python", default=sys.executable)
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()

    import imageio_ffmpeg
    from meetingvar import config
    from meetingvar.llm import chat, chat_result
    from meetingvar.monitor.hotwords import HotwordMonitor, InterestScorer
    
    config.LLM_BASE_URL = config.LLM_BASE_URL or "http://127.0.0.1:11439/v1"
    config.LLM_MODEL = config.LLM_MODEL or "meetingvar-local"
    config.LLM_API_KEY = config.LLM_API_KEY or "local"
    config.LLM_CONTEXT_TOKENS = "16384"
    config.LLM_MAX_TOKENS = "4096"
    config.LLM_THINKING = "disabled"

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    log_dir = Path(args.log_dir) if args.log_dir else Path(tempfile.mkdtemp(prefix="mv_t5_compare_"))
    log_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mv_t5_compare_") as tmp:
        for case in cases:
            wav = Path(tmp) / f"{case['id']}.wav"
            out = Path(tmp) / f"{case['id']}.txt"
            log = log_dir / f"{case['id']}.log"
            start = float(case.get("start_ratio", 0.05)) * float(case["duration_s"])
            extracted = subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(max(0, start)),
                "-t", str(args.seconds), "-i", str(case["video"]), "-vn", "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
            ], capture_output=True, timeout=180)
            if extracted.returncode or not wav.is_file():
                results.append({**case, "ok": False, "error": "excerpt extraction failed"})
                continue
            ok, elapsed = run_transcribe(args.engine_python, wav, out, log)
            if not ok:
                tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
                results.append({**case, "ok": False, "error": "transcription failed",
                                "elapsed_s": round(elapsed, 2), "tail": tail})
                continue
            rows = parse_lines(out)
            try:
                (log_dir / f"{case['id']}.txt").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
            text = " ".join(row["text"] for row in rows)
            question = str(case["question"])
            keywords = [str(x) for x in case.get("keywords", []) if x]

            l1_monitor = HotwordMonitor(keywords)
            l1_hits = l1_monitor.check(text, 0.0)
            l1_hit = bool(l1_hits)

            scorer = InterestScorer([question], threshold=0.25)
            l2_score = scorer.score(text)
            l2_hit = l2_score >= 0.25

            l3 = l3_judge(chat, question, rows)
            l3_hit = bool(l3.get("hit"))
            results.append({
                "id": case["id"], "question": question, "ok": True,
                "lines": len(rows), "elapsed_s": round(elapsed, 2),
                "l1_hit": l1_hit, "l1_terms": l1_hits,
                "l2_hit": l2_hit, "l2_score": round(l2_score, 4),
                "l3_hit": l3_hit, "l3_reason": l3.get("reason", ""),
                "l3_error": l3.get("error", ""),
            })
    total = len(results)
    report = {
        "cases": results, "cases_total": total,
        "l1_hit_rate": sum(1 for r in results if r.get("l1_hit")) / max(1, total),
        "l2_hit_rate": sum(1 for r in results if r.get("l2_hit")) / max(1, total),
        "l3_hit_rate": sum(1 for r in results if r.get("l3_hit")) / max(1, total),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        lines = ["# Test5 L1/L2/L3 comparison", "",
                 "| Case | L1 | L2 (score) | L3 | L3 reason |", "|---|---|---|---|---|"]
        for row in results:
            lines.append(f"| {row.get('id')} | {'YES' if row.get('l1_hit') else 'no'} | "
                         f"{'YES' if row.get('l2_hit') else 'no'} ({row.get('l2_score', '—')}) | "
                         f"{'YES' if row.get('l3_hit') else 'no'} | "
                         f"{row.get('l3_reason', '') or '—'} |")
        lines += ["", f"Hit rates: L1 {report['l1_hit_rate']:.1%} · "
                  f"L2 {report['l2_hit_rate']:.1%} · L3 {report['l3_hit_rate']:.1%}"]
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: round(v, 3) for k, v in report.items() if k.endswith("rate")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())