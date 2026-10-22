"""Compare ASR transcripts on the same audio (local vs cloud vs Whisper).

This reports *machine* agreement, not human CER/WER: without a human-verified
verbatim transcript, cross-engine CER is only a disagreement measure.  Every
number is reproducible from the transcript files.

Example:
  python scripts/asr_transcript_compare.py --audio-seconds 3155 \
    --transcript seaco=lecture_full.txt --elapsed seaco=469 \
    --transcript lfasr=lecture_full_lfasr.txt --elapsed lfasr=194 \
    --json report.json --md report.md
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from rapidfuzz.distance import Levenshtein

_LINE = re.compile(r"\[(?:(\d+):)?(\d+):(\d+)\]\s+([^:]+):\s+(.*)")


def parse(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        ts = int(match.group(1) or 0) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
        rows.append({"ts": ts, "speaker": match.group(4), "text": match.group(5)})
    return rows


def normalize(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).casefold()


def duplicate_pairs(rows: list[dict], window: float = 4.0) -> int:
    pairs = 0
    for index, row in enumerate(rows):
        for other in rows[index + 1:index + 8]:
            if other["ts"] - row["ts"] > window:
                break
            if SequenceMatcher(None, normalize(row["text"]),
                               normalize(other["text"])).ratio() >= 0.85:
                pairs += 1
    return pairs


def glossary_terms(path: Path | None) -> list[str]:
    if not path or not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def term_counter(text: str, terms: list[str]) -> Counter:
    if not terms:
        return Counter()
    pattern = re.compile("|".join(re.escape(term) for term in
                                  sorted(terms, key=len, reverse=True)))
    return Counter(pattern.findall(text))


def suspicious_first_line(rows: list[dict]) -> dict:
    """Flag ASR hallucination such as Whisper's repeated 组合 token."""
    if not rows:
        return {}
    text = normalize(rows[0]["text"])
    if len(text) < 60:
        return {}
    top = Counter(text).most_common(1)[0] if text else ("", 0)
    return {"chars": len(text), "top_char": top[0], "top_ratio": round(top[1] / len(text), 3),
            "suspicious": top[1] / len(text) >= 0.25}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", action="append", required=True,
                        help="label=path (repeatable)")
    parser.add_argument("--elapsed", action="append", default=[],
                        help="label=seconds wall-clock (repeatable)")
    parser.add_argument("--audio-seconds", type=float, required=True)
    parser.add_argument("--glossary", default="configs/lecture_math_terms.txt")
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()

    elapsed = {}
    for item in args.elapsed:
        label, _, value = item.partition("=")
        elapsed[label] = float(value)
    terms = glossary_terms(Path(args.glossary))
    reports, texts = [], {}
    for item in args.transcript:
        label, _, raw_path = item.partition("=")
        rows = parse(Path(raw_path))
        text = "\n".join(row["text"] for row in rows)
        texts[label] = normalize(text)
        counts = term_counter(text, terms)
        last_ts = max((row["ts"] for row in rows), default=0)
        duplicate = duplicate_pairs(rows)
        reports.append({
            "label": label,
            "file": raw_path,
            "lines": len(rows),
            "chars": len(normalize(text)),
            "last_ts_s": last_ts,
            "coverage": round(last_ts / args.audio_seconds, 3) if args.audio_seconds else None,
            "duplicate_pairs": duplicate,
            "duplicate_rate": round(duplicate / max(1, len(rows)), 3),
            "term_occurrences": sum(counts.values()),
            "term_distinct": len(counts),
            "terms": dict(counts.most_common()),
            "speakers": dict(Counter(row["speaker"] for row in rows).most_common()),
            "first_line": suspicious_first_line(rows),
            "elapsed_s": elapsed.get(label),
            "rtf": round(elapsed[label] / args.audio_seconds, 3)
            if label in elapsed and args.audio_seconds else None,
        })

    labels = [row["label"] for row in reports]
    for row in reports:
        row["cer_vs"] = {}
        for other in labels:
            if other == row["label"] or not texts[other]:
                continue
            row["cer_vs"][other] = round(
                Levenshtein.distance(texts[row["label"]], texts[other])
                / max(1, len(texts[other])), 3)

    payload = {"audio_seconds": args.audio_seconds, "transcripts": reports,
               "note": "CER is cross-engine disagreement, not human-verified CER."}
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    lines = ["| engine | lines | chars | coverage | dup rate | terms (occ/distinct) | "
             "RTF | first-line suspicious |",
             "|" + "---|" * 9]
    for row in reports:
        flag = "yes" if row["first_line"].get("suspicious") else "no"
        lines.append(
            f"| {row['label']} | {row['lines']} | {row['chars']} | {row['coverage']} | "
            f"{row['duplicate_rate']} | {row['term_occurrences']}/{row['term_distinct']} | "
            f"{row['rtf'] if row['rtf'] is not None else '—'} | {flag} |")
    lines.append("")
    lines.append("Cross-engine CER (disagreement only, not human CER):")
    for row in reports:
        for other, value in row["cer_vs"].items():
            lines.append(f"- {row['label']} vs {other}: {value}")
    table = "\n".join(lines)
    if args.md:
        Path(args.md).write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())