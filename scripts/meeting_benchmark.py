"""Run the meeting-evidence benchmark and emit JSON/Markdown reports."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.eval.evidence_benchmark import (  # noqa: E402
    evaluate_case, evaluate_root, gate, markdown)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT / "benchmarks" / "meeting_evidence"),
                        help="directory containing <case>/manifest.json")
    parser.add_argument("--case", default="", help="run one case directory instead of root")
    parser.add_argument("--json", default="", help="write JSON report to this path")
    parser.add_argument("--md", default="", help="write Markdown report to this path")
    parser.add_argument("--min-meetings", type=int, default=20)
    parser.add_argument("--min-events", type=int, default=40)
    parser.add_argument("--min-recall", type=float, default=0.8)
    parser.add_argument("--max-false-minutes-per-hour", type=float, default=10.0)
    parser.add_argument("--allow-unverified", action="store_true",
                        help="do not require human_verified labels for the gate")
    parser.add_argument("--no-gate", action="store_true", help="report metrics without gate exit")
    args = parser.parse_args()

    if args.case:
        from meetingvar.eval.evidence_benchmark import load_case
        case = evaluate_case(load_case(args.case))
        report = {"ok": True, "cases": [case], "meetings": 1,
                  "human_verified_meetings": 1 if case["labels_status"] == "human_verified" else 0,
                  "hours": case["duration_s"] / 3600.0,
                  "events_total": case["events_total"], "events_matched": case["events_matched"],
                  "event_recall_micro": case["event_recall"],
                  "event_recall_micro_ci95": case["event_recall_ci95"],
                  "clip_precision_micro": case["clip_precision"],
                  "false_saved_minutes_per_hour": case["false_saved_minutes_per_hour"],
                  "answers_total": case["answers_total"],
                  "citation_support_rate_micro": case["citation_support_rate"]}
    else:
        report = evaluate_root(args.root)

    result = None if args.no_gate else gate(
        report, min_meetings=args.min_meetings, min_events=args.min_events,
        min_recall=args.min_recall,
        max_false_minutes_per_hour=args.max_false_minutes_per_hour,
        require_human=not args.allow_unverified)
    if args.json:
        Path(args.json).write_text(json.dumps({"report": report, "gate": result},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(markdown(report, result), encoding="utf-8")
    print(json.dumps({"meetings": report.get("meetings"),
                      "events": report.get("events_total"),
                      "recall": report.get("event_recall_micro"),
                      "false_minutes_per_hour": report.get("false_saved_minutes_per_hour"),
                      "gate": result}, ensure_ascii=False, indent=2))
    return 0 if result is None or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())