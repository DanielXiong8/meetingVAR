"""Find timestamped focus segments in a lecture transcript.

Example:
  python scripts/lecture_focus.py --transcript lecture.txt \
    --question "方向导数与梯度如何定义？" --keywords 方向导数,梯度
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.lecture import find_focus, parse_transcript  # noqa: E402
from meetingvar.llm import chat  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--window", type=float, default=90.0)
    parser.add_argument("--stride", type=float, default=45.0)
    parser.add_argument("--json", default="")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    config.LLM_BASE_URL = config.LLM_BASE_URL or "http://127.0.0.1:11439/v1"
    config.LLM_MODEL = config.LLM_MODEL or "meetingvar-local"
    config.LLM_API_KEY = config.LLM_API_KEY or "local"
    config.LLM_CONTEXT_TOKENS = config.LLM_CONTEXT_TOKENS or "16384"
    config.LLM_MAX_TOKENS = config.LLM_MAX_TOKENS or "4096"
    config.LLM_THINKING = "disabled"
    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    turns = parse_transcript(args.transcript)
    result = find_focus(turns, args.question, keywords=keywords,
                        chat_fn=None if args.no_llm else chat,
                        window=args.window, stride=args.stride,
                        max_candidates=5, timeout=120)
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    for segment in result["segments"]:
        print(f"[{int(segment['start'])//60:02d}:{int(segment['start'])%60:02d}-"
              f"{int(segment['end'])//60:02d}:{int(segment['end'])%60:02d}] "
              f"{segment['reason']} · {segment['text'][:120]}")
    print(json.dumps({"hits": result["hits"], "windows": result["windows"],
                      "candidates": result["candidates"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())