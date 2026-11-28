#!/usr/bin/env python3
"""CLI: locate evidence in a recording transcript and build a verifiable pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_core import TOOL_NAME, build_pack, parse_transcript, search  # noqa: E402

DISCLAIMER = (
    "本工具只做录音材料的辅助定位与完整性校验，不构成法律意见，"
    "也不代表司法机关会采信。请仅处理你合法持有的录音，并遵守当地法律。"
)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}小时{minutes}分{sec}秒"
    return f"{minutes}分{sec}秒" if minutes else f"{sec}秒"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从录音/录屏转写中定位与一条事实主张相关的完整语境和核心证据。"
    )
    parser.add_argument("--transcript", required=True,
                        help="转写文件：.json/.srt/.vtt/.txt/.md")
    parser.add_argument("--claim", required=True,
                        help="事实主张，例如：他什么时候承认延迟交付")
    parser.add_argument("--out", default="out", help="输出目录（默认 ./out）")
    parser.add_argument("--media", default="", help="可选：原始录音/录屏，用于截取片段")
    parser.add_argument("--case-id", default="", help="可选：案件/材料编号，写入 manifest")
    parser.add_argument("--top", type=int, default=3, help="返回片段数（默认 3）")
    parser.add_argument("--min-span", type=float, default=90.0,
                        help="完整语境的最短秒数（默认 90）")
    parser.add_argument("--max-span", type=float, default=0.0,
                        help="完整语境的最长秒数；0=按录音长度自动（默认 0）")
    parser.add_argument("--core-min", type=float, default=25.0,
                        help="核心证据最短秒数（默认 25）")
    parser.add_argument("--core-max", type=float, default=75.0,
                        help="核心证据最长秒数（默认 75）")
    parser.add_argument("--rel-threshold", type=float, default=0.15,
                        help="继续扩展的相关度阈值，相对峰值（默认 0.15）")
    parser.add_argument("--bridge", type=int, default=4,
                        help="允许跨过几个低相关句（默认 4）")
    parser.add_argument("--max-gap", type=float, default=20.0,
                        help="相邻句超过多少秒视为换话题（默认 20）")
    parser.add_argument("--no-pack", action="store_true", help="只输出结果，不生成证据包")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    args = parser.parse_args(argv)

    try:
        _meta, turns = parse_transcript(args.transcript)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[{TOOL_NAME}] 无法读取转写：{exc}", file=sys.stderr)
        return 2

    segments = search(
        turns,
        args.claim,
        top=max(1, args.top),
        min_span=args.min_span,
        max_span=args.max_span,
        core_min=args.core_min,
        core_max=args.core_max,
        rel_threshold=args.rel_threshold,
        bridge=args.bridge,
        max_gap=args.max_gap,
    )
    if not segments:
        print(f"[{TOOL_NAME}] 没有找到匹配片段。建议把主张写得更具体，"
              f"例如“他什么时候承认延迟交付”，或补充可能的原话。", file=sys.stderr)
        return 1

    notes: list[str] = []
    if not args.media:
        notes.append("未提供 --media，未生成可播放证据片段；时间戳与转写仍可复核。")

    pack = build_pack(
        args.out,
        query=args.claim,
        transcript_path=args.transcript,
        segments=segments,
        media_path=args.media or None,
        notes=notes,
        case_id=args.case_id,
        disclaimer=DISCLAIMER,
        report_name="evidence_report.md",
        excerpt_name="evidence_excerpt.txt",
    ) if not args.no_pack else None

    if args.json:
        print(json.dumps({"segments": segments, "pack": pack,
                          "disclaimer": DISCLAIMER}, ensure_ascii=False, indent=2))
        return 0

    print(f"[{TOOL_NAME}] 找到 {len(segments)} 段证据候选：")
    for index, segment in enumerate(segments, 1):
        print(f"  {index}. 完整语境 {segment['start_text']} – {segment['end_text']} "
              f"（{_fmt_duration(segment['duration'])}）")
        print(f"     核心证据 {segment['core_start_text']} – {segment['core_end_text']} "
              f"（{_fmt_duration(segment['core_duration'])}）")
        print(f"     {segment['quote'][:140]}")
    if pack:
        print(f"\n证据包：{pack['zip']}")
        print(f"报告：{pack['report']}")
    print(f"\n边界说明：{DISCLAIMER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())