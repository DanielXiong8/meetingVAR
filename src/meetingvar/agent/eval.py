"""Agent evaluation harness: golden questions, citations, refusal and efficiency.

This is deliberately separate from unit tests. Unit tests prove code contracts;
this harness measures whether the agent actually answers with the right evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    expected_citations: tuple[str, ...] = ()
    expect_refusal: bool = False
    max_steps: int = 8
    tags: tuple[str, ...] = ()


@dataclass
class EvalRow:
    case_id: str
    ok: bool
    grounded: bool
    answered: bool
    expected_refusal: bool
    citation_precision: float
    citation_recall: float
    steps: int
    tool_calls: int
    elapsed_ms: float
    input_tokens: int
    output_tokens: int
    stop_reason: str
    errors: list = field(default_factory=list)


def load_cases(path: Path) -> list[EvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("eval file must contain a cases array")
    cases = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("question"), str):
            continue
        cases.append(EvalCase(
            case_id=str(row.get("id") or f"case_{index}"),
            question=row["question"],
            expected_citations=tuple(str(x) for x in row.get("expected_citations", [])),
            expect_refusal=bool(row.get("expect_refusal", False)),
            max_steps=max(1, min(20, int(row.get("max_steps", 8)))),
            tags=tuple(str(x) for x in row.get("tags", [])),
        ))
    return cases


def _citation_text(report: dict) -> str:
    parts = []
    for citation in report.get("citations", []):
        if not isinstance(citation, dict):
            continue
        if citation.get("name"):
            parts.append(str(citation["name"]))
        parts.extend(str(tid) for tid in citation.get("turn_ids", []) or [])
        if citation.get("quote"):
            parts.append(str(citation["quote"]))
    return "\n".join(parts)


def score_report(case: EvalCase, report: dict) -> EvalRow:
    citations = report.get("citations") or []
    invalid = report.get("invalid_citations") or []
    precision = (len(citations) / (len(citations) + len(invalid))
                 if citations or invalid else 1.0)
    text = _citation_text(report)
    expected = list(case.expected_citations)
    matched = sum(1 for expected_item in expected if expected_item in text)
    recall = matched / len(expected) if expected else 1.0
    answered = bool(report.get("answered"))
    grounded = bool(report.get("grounded"))
    refusal_ok = (not answered) if case.expect_refusal else True
    usage = report.get("usage") or {}
    return EvalRow(
        case_id=case.case_id, ok=bool(report.get("ok")) and refusal_ok,
        grounded=grounded, answered=answered, expected_refusal=case.expect_refusal,
        citation_precision=round(precision, 3), citation_recall=round(recall, 3),
        steps=int(report.get("steps") or 0), tool_calls=int(report.get("tool_calls") or 0),
        elapsed_ms=float(report.get("elapsed_ms") or 0),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        stop_reason=str(report.get("stop_reason") or ""),
        errors=list(report.get("errors") or []))


def summarize(rows: Iterable[EvalRow]) -> dict:
    rows = list(rows)
    total = len(rows)
    if not total:
        return {"total": 0, "accuracy": 0.0, "grounded_rate": 0.0,
                "citation_precision": 0.0, "citation_recall": 0.0,
                "refusal_accuracy": 0.0, "avg_steps": 0.0, "avg_tool_calls": 0.0,
                "avg_elapsed_ms": 0.0, "input_tokens": 0, "output_tokens": 0}
    refusal_rows = [row for row in rows if row.expected_refusal]
    return {
        "total": total,
        "accuracy": round(sum(1 for row in rows if row.ok) / total, 3),
        "grounded_rate": round(sum(1 for row in rows if row.grounded) / total, 3),
        "citation_precision": round(sum(row.citation_precision for row in rows) / total, 3),
        "citation_recall": round(sum(row.citation_recall for row in rows) / total, 3),
        "refusal_accuracy": round(
            sum(1 for row in refusal_rows if not row.answered) / max(1, len(refusal_rows)), 3)
        if refusal_rows else 1.0,
        "avg_steps": round(sum(row.steps for row in rows) / total, 2),
        "avg_tool_calls": round(sum(row.tool_calls for row in rows) / total, 2),
        "avg_elapsed_ms": round(sum(row.elapsed_ms for row in rows) / total, 1),
        "input_tokens": sum(row.input_tokens for row in rows),
        "output_tokens": sum(row.output_tokens for row in rows),
        "stop_reasons": {reason: sum(1 for row in rows if row.stop_reason == reason)
                         for reason in sorted({row.stop_reason for row in rows})},
    }


def evaluate(cases: Iterable[EvalCase], runner: Callable[[EvalCase], dict]) -> dict:
    rows = []
    for case in cases:
        report = runner(case)
        rows.append(score_report(case, report))
    return {"rows": [row.__dict__ for row in rows], "summary": summarize(rows)}