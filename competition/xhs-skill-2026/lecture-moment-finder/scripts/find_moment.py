#!/usr/bin/env python3
"""CLI: locate the full stretch a lecture covers a topic and write replay outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from moment_core import TOOL_NAME, build_outputs, parse_transcript, search  # noqa: E402


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}小时{minutes}分{sec}秒"
    return f"{minutes}分{sec}秒" if minutes else f"{sec}秒"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从课堂录音/录屏的转写文本中定位老师讲某个知识点的完整段落。"
    )
    parser.add_argument("--transcript", required=True,
                        help="转写文件：.json/.srt/.vtt/.txt/.md")
    parser.add_argument("--query", required=True,
                        help="自然语言问题，例如：老师讲动态规划状态转移方程的那几分钟")
    parser.add_argument("--out", default="out", help="输出目录（默认 ./out）")
    parser.add_argument("--media", default="", help="可选：原始录音/录屏，用于截取片段")
    parser.add_argument("--top", type=int, default=3, help="返回片段数（默认 3）")
    parser.add_argument("--min-span", type=float, default=45.0,
                        help="完整讨论的最短秒数（默认 45）")
    parser.add_argument("--max-span", type=float, default=0.0,
                        help="完整讨论的最长秒数；0=按录音长度自动（默认 0）")
    parser.add_argument("--core-min", type=float, default=30.0,
                        help="核心片段最短秒数（默认 30）")
    parser.add_argument("--core-max", type=float, default=90.0,
                        help="核心片段最长秒数（默认 90）")
    parser.add_argument("--rel-threshold", type=float, default=0.15,
                        help="继续扩展的相关度阈值，相对峰值（默认 0.15）")
    parser.add_argument("--bridge", type=int, default=2,
                        help="允许跨过几个低相关句（默认 2）")
    parser.add_argument("--max-gap", type=float, default=20.0,
                        help="相邻句超过多少秒视为换话题（默认 20）")
    parser.add_argument("--no-save", "--no-pack", dest="no_save", action="store_true",
                        help="只输出结果，不写 result/report/clip 文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    args = parser.parse_args(argv)

    try:
        _meta, turns = parse_transcript(args.transcript)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[{TOOL_NAME}] 无法读取转写：{exc}", file=sys.stderr)
        return 2

    segments = search(
        turns,
        args.query,
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
        print(f"[{TOOL_NAME}] 没有找到匹配片段。建议把问题换成一个具体知识点，"
              f"例如“老师讲二叉树的遍历”。", file=sys.stderr)
        return 1

    notes: list[str] = []
    if not args.media:
        notes.append("未提供 --media，未生成可播放片段；时间戳与转写仍然可复核。")

    outputs = build_outputs(
        args.out,
        query=args.query,
        transcript_path=args.transcript,
        segments=segments,
        media_path=args.media or None,
        notes=notes,
    ) if not args.no_save else None

    if args.json:
        print(json.dumps({"segments": segments, "outputs": outputs},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"[{TOOL_NAME}] 找到 {len(segments)} 段：")
    for index, segment in enumerate(segments, 1):
        print(f"  {index}. 完整讨论 {segment['start_text']} – {segment['end_text']} "
              f"（{_fmt_duration(segment['duration'])}）")
        print(f"     核心片段 {segment['core_start_text']} – {segment['core_end_text']} "
              f"（{_fmt_duration(segment['core_duration'])}）")
        print(f"     {segment['quote'][:120]}")
    if outputs:
        print(f"\n输出目录：{outputs['out_dir']}")
        print(f"报告：{outputs['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())