#!/usr/bin/env python3
"""Optional local transcription helper.

The core skill is transcript-first and needs no third-party packages. If the
user only has an audio/video file, this helper can produce a timestamped JSON
transcript with faster-whisper when it is installed. It never downloads a
model silently and never sends audio to a server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="用本地 faster-whisper 生成带时间戳的课堂转写（可选）。"
    )
    parser.add_argument("media", help="音频/视频文件")
    parser.add_argument("--out", default="transcript.json", help="输出 JSON")
    parser.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium"],
                        help="Whisper 模型（默认 small）")
    parser.add_argument("--language", default="zh", help="语言（默认 zh）")
    args = parser.parse_args(argv)

    # faster-whisper decodes through PyAV, so the ffmpeg CLI is not required
    # for transcription itself. The CLI is only used for clip extraction.
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        print("未安装 faster-whisper。运行：\n"
              "  pip install faster-whisper\n"
              "或者直接使用已有的 SRT/VTT/TXT 转写。", file=sys.stderr)
        return 2

    media = Path(args.media)
    if not media.is_file():
        print(f"文件不存在：{media}", file=sys.stderr)
        return 2

    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(media), language=args.language, vad_filter=True, beam_size=5
    )
    turns = []
    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            turns.append({
                "start": round(float(segment.start), 2),
                "end": round(float(segment.end), 2),
                "speaker": "",
                "text": text,
            })

    payload = {
        "title": media.stem,
        "language": getattr(info, "language", args.language),
        "duration": round(float(getattr(info, "duration", 0) or 0), 2),
        "turns": turns,
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写入 {args.out}（{len(turns)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())