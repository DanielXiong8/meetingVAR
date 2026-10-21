"""Event-level benchmark for selective meeting evidence.

The product promise is not "record everything"; it is "save the moments that
matter without losing important evidence".  This module makes that claim
measurable on real meetings:

* labels describe required-capture events with start/end times;
* predictions describe clips that the current mechanism actually persisted;
* metrics report event recall, clip precision, false persisted seconds and
  focus coverage.

The benchmark is deliberately data-agnostic: labels can be a JSON skeleton or
a CSV workbook.  Private meeting content never needs to enter the repository.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class Event:
    event_id: str
    start: float
    end: float
    event_type: str = ""
    status: str = ""
    focus_ids: list[str] = field(default_factory=list)
    requires_capture: bool = True
    evidence_turn_ids: list = field(default_factory=list)
    needs_review: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Clip:
    clip_id: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Case:
    case_id: str
    title: str
    duration_s: float
    events: list[Event]
    clips: list[Clip]
    labels_status: str = "unverified"
    source: str = ""
    answers: list[dict] = field(default_factory=list)


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "需要", "required"}


def _focus_ids(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def load_events(path: Path) -> tuple[list[Event], dict]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(path.open("r", encoding="utf-8-sig", newline="")))
        events = []
        for row in rows:
            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                continue
            events.append(Event(
                event_id=event_id,
                start=_number(row.get("start_time") or row.get("t0_s")),
                end=_number(row.get("end_time") or row.get("t1_s") or row.get("t0_s")),
                event_type=str(row.get("event_type") or ""),
                status=str(row.get("event_status") or ""),
                focus_ids=_focus_ids(row.get("focus_ids")),
                requires_capture=_truthy(row.get("requires_capture", "true")),
                evidence_turn_ids=[x for x in str(row.get("evidence_turn_ids") or "").replace(";", ",").split(",") if x.strip()],
                needs_review=_truthy(row.get("needs_human_review")),
            ))
        return events, {"status": "unverified", "format": "csv"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"events": data}
    events = []
    for row in data.get("events", []):
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            continue
        start = _number(row.get("start_time", row.get("start_s", row.get("t0_s"))))
        end = _number(row.get("end_time", row.get("end_s", row.get("t1_s"))), start)
        focus = row.get("focus_ids") or row.get("focus") or []
        events.append(Event(
            event_id=event_id, start=start, end=max(start, end),
            event_type=str(row.get("event_type") or ""),
            status=str(row.get("event_status") or ""),
            focus_ids=_focus_ids(focus),
            requires_capture=_truthy(row.get("requires_capture", True)),
            evidence_turn_ids=row.get("evidence_turn_ids") or [],
            needs_review=bool(row.get("needs_human_review") or
                              str(row.get("annotator_confidence", "")).startswith("unverified")),
        ))
    return events, {"status": str(data.get("status") or "unverified"),
                    "format": "json", "notes": data.get("annotation_notes") or []}


def load_clips(source: dict | Path) -> list[Clip]:
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(data, list):
        clips = data
    elif isinstance(data, dict) and isinstance(data.get("runs"), list):
        clips = []
        for run in data["runs"]:
            for clip in run.get("clips", []) if isinstance(run, dict) else []:
                clips.append(clip)
    elif isinstance(data, dict) and isinstance(data.get("clips"), list):
        clips = data["clips"]
    elif isinstance(data, dict) and data.get("clip_window_s"):
        clips = [{"start_time": data["clip_window_s"][0],
                  "end_time": data["clip_window_s"][1],
                  "clip_id": "current_mechanism"}]
    else:
        clips = []
    result = []
    for index, row in enumerate(clips):
        if not isinstance(row, dict):
            continue
        start = _number(row.get("start_time", row.get("start_s", row.get("t0_s"))))
        end = _number(row.get("end_time", row.get("end_s", row.get("t1_s"))), start)
        result.append(Clip(str(row.get("clip_id") or row.get("id") or f"clip_{index}"),
                           start, max(start, end)))
    return result


def merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    rows = sorted((max(0.0, a), max(0.0, b)) for a, b in intervals if b > a)
    merged = []
    for start, end in rows:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(a, b) for a, b in merged]


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _union_duration(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))


def _wilson(success: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = success / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def evaluate_case(case: Case, *, min_event_coverage: float = 0.5,
                  clip_event_overlap: float = 0.1) -> dict:
    required = [event for event in case.events if event.requires_capture and event.duration > 0]
    clip_intervals = [(clip.start, clip.end) for clip in case.clips]
    saved = _union_duration(clip_intervals)
    event_intervals = [(event.start, event.end) for event in required]

    matches = []
    missing = []
    focus_hit = {focus: False for event in required for focus in event.focus_ids}
    for event in required:
        best = max((overlap(event.start, event.end, clip.start, clip.end) /
                    event.duration for clip in case.clips), default=0.0)
        matched = best >= min_event_coverage
        matches.append({"event_id": event.event_id, "coverage": round(best, 4),
                        "matched": matched, "type": event.event_type,
                        "start": event.start, "end": event.end})
        if matched:
            for focus in event.focus_ids:
                focus_hit[focus] = True
        else:
            missing.append(event.event_id)

    related_clips = []
    false_clips = []
    for clip in case.clips:
        overlap_any = max((overlap(clip.start, clip.end, event.start, event.end)
                           for event in required), default=0.0)
        related = overlap_any >= max(1.0, clip.duration * clip_event_overlap)
        (related_clips if related else false_clips).append({
            "clip_id": clip.clip_id, "start": clip.start, "end": clip.end,
            "duration": round(clip.duration, 3), "overlap_s": round(overlap_any, 3),
        })

    # Extra persisted time: clip intervals outside every labeled event.
    extra = []
    for start, end in merge_intervals(clip_intervals):
        cursor = start
        for event_start, event_end in merge_intervals(event_intervals):
            if event_end <= cursor or event_start >= end:
                continue
            if event_start > cursor:
                extra.append((cursor, min(event_start, end)))
            cursor = max(cursor, event_end)
            if cursor >= end:
                break
        if cursor < end:
            extra.append((cursor, end))
    false_seconds = _union_duration(extra)
    duration_hours = max(case.duration_s, 1.0) / 3600.0
    recall = sum(1 for row in matches if row["matched"]) / len(required) if required else None
    interval = _wilson(sum(1 for row in matches if row["matched"]), len(required)) if required else None
    focus_total = len(focus_hit)
    focus_covered = sum(1 for hit in focus_hit.values() if hit)
    answers = [row for row in case.answers if isinstance(row, dict)]
    supported = [row for row in answers if row.get("supported") is True]
    return {
        "case_id": case.case_id, "title": case.title, "source": case.source,
        "labels_status": case.labels_status,
        "duration_s": case.duration_s,
        "events_total": len(required),
        "events_matched": sum(1 for row in matches if row["matched"]),
        "event_recall": recall,
        "event_recall_ci95": interval,
        "clip_count": len(case.clips),
        "clips_related": len(related_clips),
        "clips_false": len(false_clips),
        "clip_precision": (len(related_clips) / len(case.clips)) if case.clips else None,
        "saved_seconds": round(saved, 3),
        "saved_minutes_per_hour": round(saved / 60.0 / duration_hours, 3),
        "false_saved_seconds": round(false_seconds, 3),
        "false_saved_minutes_per_hour": round(false_seconds / 60.0 / duration_hours, 3),
        "focus_total": focus_total,
        "focus_covered": focus_covered,
        "focus_coverage": (focus_covered / focus_total) if focus_total else None,
        "answers_total": len(answers),
        "citation_support_rate": (len(supported) / len(answers)) if answers else None,
        "matches": matches,
        "missing_events": missing,
        "false_clips": false_clips,
    }


def load_case(case_dir: str | Path) -> Case:
    case_dir = Path(case_dir)
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    labels_path = case_dir / str(manifest.get("labels") or "labels.json")
    events, label_meta = load_events(labels_path)
    predictions = manifest.get("predictions")
    if predictions:
        clips = load_clips(case_dir / predictions)
    else:
        clips = []
    answers = []
    if manifest.get("answers"):
        answer_path = case_dir / manifest["answers"]
        data = json.loads(answer_path.read_text(encoding="utf-8"))
        answers = data if isinstance(data, list) else data.get("answers", [])
    status = str(manifest.get("labels_status") or label_meta.get("status") or "unverified")
    if status.lower() in {"machine_seed", "unverified_machine_seed"} or "unverified" in status.lower():
        status = "unverified"
    return Case(case_id=str(manifest.get("case_id") or case_dir.name),
                title=str(manifest.get("title") or case_dir.name),
                duration_s=_number(manifest.get("duration_s")),
                events=events, clips=clips, labels_status=status,
                source=str(labels_path), answers=answers)


def load_root(root: str | Path) -> list[Case]:
    root = Path(root)
    cases = []
    for manifest in sorted(root.glob("*/manifest.json")):
        try:
            cases.append(load_case(manifest.parent))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return cases


def evaluate_root(root: str | Path, *, min_event_coverage: float = 0.5,
                  clip_event_overlap: float = 0.1) -> dict:
    cases = [evaluate_case(case, min_event_coverage=min_event_coverage,
                           clip_event_overlap=clip_event_overlap)
             for case in load_root(root)]
    total = sum(row["events_total"] for row in cases)
    matched = sum(row["events_matched"] for row in cases)
    clips = sum(row["clip_count"] for row in cases)
    related = sum(row["clips_related"] for row in cases)
    hours = sum(row["duration_s"] for row in cases) / 3600.0
    human = sum(1 for row in cases if row["labels_status"] == "human_verified")
    return {
        "ok": True, "root": str(root), "cases": cases,
        "meetings": len(cases), "human_verified_meetings": human,
        "hours": round(hours, 3),
        "events_total": total, "events_matched": matched,
        "event_recall_micro": (matched / total) if total else None,
        "event_recall_micro_ci95": _wilson(matched, total) if total else None,
        "clip_precision_micro": (related / clips) if clips else None,
        "false_saved_minutes_per_hour": (sum(row["false_saved_seconds"] for row in cases)
                                         / 60.0 / hours) if hours else None,
        "answers_total": sum(row["answers_total"] for row in cases),
        "citation_support_rate_micro": (
            sum((row["citation_support_rate"] or 0) * row["answers_total"] for row in cases)
            / max(1, sum(row["answers_total"] for row in cases))
            if sum(row["answers_total"] for row in cases) else None),
    }


def gate(report: dict, *, min_meetings: int = 20, min_events: int = 40,
         min_recall: float = 0.8, max_false_minutes_per_hour: float = 10.0,
         require_human: bool = True) -> dict:
    checks = {
        "meetings": report.get("meetings", 0) >= min_meetings,
        "events": report.get("events_total", 0) >= min_events,
        "recall": (report.get("event_recall_micro") or 0) >= min_recall,
        "false_save": (report.get("false_saved_minutes_per_hour") or 0) <= max_false_minutes_per_hour,
        "human_labels": (report.get("human_verified_meetings", 0) >= min_meetings) if require_human else True,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "thresholds": {"min_meetings": min_meetings, "min_events": min_events,
                           "min_recall": min_recall,
                           "max_false_minutes_per_hour": max_false_minutes_per_hour,
                           "require_human": require_human}}


def markdown(report: dict, gate_result: dict | None = None) -> str:
    lines = ["# Meeting evidence benchmark", "",
             f"Meetings: {report.get('meetings', 0)} · human verified: "
             f"{report.get('human_verified_meetings', 0)} · hours: {report.get('hours', 0)}", "",
             "| Case | Labels | Events | Recall | Clip precision | False save min/h | Saved min/h |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in report.get("cases", []):
        recall = "—" if row["event_recall"] is None else f"{row['event_recall']:.1%}"
        precision = "—" if row["clip_precision"] is None else f"{row['clip_precision']:.1%}"
        lines.append(f"| {row['case_id']} | {row['labels_status']} | {row['events_total']} | "
                     f"{recall} | {precision} | {row['false_saved_minutes_per_hour']:.2f} | "
                     f"{row['saved_minutes_per_hour']:.2f} |")
    lines += ["", f"Micro recall: {report.get('event_recall_micro')}", ""]
    for row in report.get("cases", []):
        if row.get("missing_events"):
            lines.append(f"- {row['case_id']} missing: {', '.join(row['missing_events'])}")
    if gate_result:
        lines += ["", "## Gate", f"**{'PASS' if gate_result['passed'] else 'FAIL'}**"]
        for name, ok in gate_result["checks"].items():
            lines.append(f"- {name}: {'ok' if ok else 'FAIL'}")
    return "\n".join(lines) + "\n"