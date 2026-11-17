"""Independent, offline task-level answer evaluation.

The candidate's ok/grounded/answered/semantic_verified flags are diagnostics,
never labels. Source snapshots and rubrics belong to the evaluator, not to the
candidate. Structural checks alone cannot establish semantic correctness.

This module deliberately imports no application, provider, embedding or config
modules. Run this file directly (python -I eval.py) for local A/B/C replay; do
not use the application's agent-eval CLI for an offline-only experiment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping


@dataclass(frozen=True)
class EvalCase:
    # Keep the original positional constructor and public entry points.
    case_id: str
    question: str
    expected_citations: tuple[str, ...] = ()
    expect_refusal: bool = False
    max_steps: int = 8
    tags: tuple[str, ...] = ()
    sources: tuple[dict, ...] = ()
    required_claims: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    # None means no enumeration task; () explicitly means an empty expected set.
    enumeration: tuple[dict, ...] | None = None
    scope: dict = field(default_factory=dict)
    history: tuple[dict, ...] = ()
    constraints: tuple[dict, ...] = ()
    expected_units: tuple[str, ...] = ()
    require_complete_coverage: bool = False
    label_origin: str = "unlabeled"
    annotation_id: str = ""
    # Exact, whitespace-normalised outputs: only a synthetic program oracle.
    oracle_answers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionEvidence:
    """Out-of-band harness receipts, not candidate-generated report fields."""

    read_units: tuple[str, ...] = ()
    empty_units: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticReview:
    """Independent review bound to the exact case and candidate answer.

    Review files are trusted evaluator inputs, not authenticated signatures.
    A reviewer name is provenance, not proof of a human or service identity.
    """

    case_id: str
    case_digest: str
    answer_digest: str
    reviewer: str
    method: str
    verdict: str
    rationale: str


@dataclass(frozen=True)
class RunOutcome:
    report: dict
    execution: ExecutionEvidence | None = None


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
    status: str = "unverified"
    quality_status: str = "unverified"
    semantic_status: str = "unverified"
    verification_basis: str = "none"
    label_origin: str = "unlabeled"
    source_validated: bool = False
    citation_checks: list = field(default_factory=list)
    missing_claims: list = field(default_factory=list)
    forbidden_claims_found: list = field(default_factory=list)
    missing_items: list = field(default_factory=list)
    extra_items: list = field(default_factory=list)
    duplicate_items: list = field(default_factory=list)
    constraint_violations: list = field(default_factory=list)
    coverage_status: str = "not_required"
    missing_units: list = field(default_factory=list)
    infra_failures: list = field(default_factory=list)
    unverified_reasons: list = field(default_factory=list)
    budget_exceeded: bool = False
    reported_ok: bool = False
    reported_grounded: bool = False
    reported_answered: bool = False
    reported_semantic_verified: bool = False


def _normal(text: str) -> str:
    return " ".join(text.split())


def _digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_revision(pages: list[dict]) -> str:
    """Content-address an evaluator-owned page/line snapshot."""
    return "sha256:" + _digest(pages)


def case_digest(case: EvalCase) -> str:
    return _digest(asdict(case))


def answer_digest(report: dict) -> str:
    # Bind reviews to the actual text and submitted citations, not green flags.
    return _digest({"answer": report.get("answer"),
                    "citations": report.get("citations", [])})


def _strings(value, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
            not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{name} must be an array of nonempty strings")
    return tuple(value)


def _validate_case(case: EvalCase) -> None:
    if not case.case_id or not isinstance(case.question, str):
        raise ValueError("case requires an id and a question")
    if case.label_origin not in {"unlabeled", "human", "synthetic"}:
        raise ValueError("label_origin must be unlabeled, human, or synthetic")
    if case.oracle_answers and case.label_origin != "synthetic":
        raise ValueError("program oracles are only allowed on synthetic cases")
    for name in ("expected_citations", "tags", "required_claims",
                 "forbidden_claims", "expected_units", "oracle_answers"):
        _strings(getattr(case, name), name)
    if type(case.max_steps) is not int or case.max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    if not isinstance(case.scope, dict):
        raise ValueError("scope must be an object")
    unknown = set(case.scope) - {"source_ids", "pages"}
    if unknown:
        raise ValueError(f"unsupported scope fields: {sorted(unknown)}")
    if "source_ids" in case.scope:
        _strings(case.scope["source_ids"], "scope.source_ids")
    for bounds in case.scope.get("pages", {}).values():
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(n) is not int or n < 1 for n in bounds)
                or bounds[0] > bounds[1]):
            raise ValueError("scope page bounds must be positive ordered pairs")
    seen = set()
    for source in case.sources:
        if not isinstance(source, dict):
            raise ValueError("source snapshot must be an object")
        sid, revision, pages = (source.get(k) for k in ("source_id", "revision", "pages"))
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError("source ids must be nonempty and unique within a case")
        seen.add(sid)
        if not isinstance(revision, str) or not revision or revision == "unversioned":
            raise ValueError("evaluator sources require an explicit revision")
        if not isinstance(pages, list) or not pages:
            raise ValueError("source snapshot requires pages")
        numbers = []
        for page in pages:
            if not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1:
                raise ValueError("snapshot pages require positive integer page numbers")
            lines = page.get("lines")
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise ValueError("snapshot page lines must be strings")
            numbers.append(page["page"])
        if len(numbers) != len(set(numbers)) or numbers != sorted(numbers):
            raise ValueError("snapshot pages must be unique and sorted")
        if revision.startswith("sha256:") and revision != source_revision(pages):
            raise ValueError("snapshot content does not match its revision")
    for turn in case.history:
        if (not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant", "system"}
                or not isinstance(turn.get("content"), str)):
            raise ValueError("history must contain role/content turns")
    for rule in case.constraints:
        if not isinstance(rule, dict) or not rule.get("id"):
            raise ValueError("constraint requires an id")
        if set(rule) - {"id", "required_claims", "forbidden_claims"}:
            raise ValueError("unsupported constraint field")
        _strings(rule.get("required_claims", []), "constraint.required_claims")
        _strings(rule.get("forbidden_claims", []), "constraint.forbidden_claims")
    if case.enumeration is not None:
        ids, aliases = set(), set()
        for item in case.enumeration:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("enumeration items require ids")
            if item["id"] in ids:
                raise ValueError("duplicate enumeration id")
            ids.add(item["id"])
            forms = _strings(item.get("forms", [item["id"]]), "enumeration.forms")
            if not forms:
                raise ValueError("enumeration forms cannot be empty")
            normalized = {_normal(form) for form in forms}
            if aliases & normalized:
                raise ValueError("ambiguous enumeration alias")
            aliases |= normalized


def _parse_cases(data) -> list[EvalCase]:
    rows = data.get("cases") if isinstance(data, dict) else data
    sources = data.get("sources", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        raise ValueError("eval file must contain a cases array")
    cases = []
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("question"), str):
            raise ValueError(f"invalid eval case at position {index}")
        fields = dict(row)
        fields["case_id"] = str(fields.pop("id", fields.get("case_id", f"case_{index}")))
        fields.setdefault("sources", copy.deepcopy(sources))
        for key in ("expected_citations", "tags", "sources", "required_claims",
                    "forbidden_claims", "history", "constraints", "expected_units", "oracle_answers"):
            if key in fields:
                if not isinstance(fields[key], list):
                    raise ValueError(f"{key} must be an array")
                fields[key] = tuple(fields[key])
        if fields.get("enumeration") is not None:
            if not isinstance(fields["enumeration"], list):
                raise ValueError("enumeration must be an array or null")
            fields["enumeration"] = tuple(fields["enumeration"])
        try:
            case = EvalCase(**fields)
        except TypeError as exc:
            raise ValueError(f"invalid eval case {index}: {exc}") from exc
        _validate_case(case)
        if case.case_id in seen:
            raise ValueError(f"duplicate case id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return cases


def load_cases(path: Path) -> list[EvalCase]:
    return _parse_cases(json.loads(Path(path).read_text(encoding="utf-8")))


def _check_citation(case: EvalCase, citation) -> dict:
    result = {"source_id": "", "status": "failed", "reasons": []}
    if not isinstance(citation, dict):
        result["reasons"].append("invalid_citation_type")
        return result
    sid = citation.get("source_id") or citation.get("source_ref_id") or citation.get("id")
    result["source_id"] = str(sid or "")
    source = next((s for s in case.sources if s["source_id"] == sid), None)
    if source is None:
        result["status"] = "failed" if case.sources else "unverified"
        result["reasons"].append("unknown_source" if case.sources else "no_independent_snapshot")
        return result
    reasons = result["reasons"]
    if citation.get("revision") != source["revision"]:
        reasons.append("source_revision_mismatch")
    if citation.get("path") and citation.get("path") != source.get("path"):
        reasons.append("source_path_mismatch")
    pages = {page["page"]: page["lines"] for page in source["pages"]}
    low = citation.get("page_start", citation.get("page"))
    high = citation.get("page_end", low)
    has_lines = "line_start" in citation or "line_end" in citation
    if low is None and high is None and has_lines and len(pages) == 1:
        low = high = next(iter(pages))
    valid_pages = (type(low) is int and type(high) is int and
                   1 <= low <= high and high - low < len(pages) and
                   all(n in pages for n in range(low, high + 1)))
    selected = []
    if not valid_pages:
        reasons.append("invalid_page_locator")
    else:
        if has_lines:
            start = citation.get("line_start")
            end = citation.get("line_end", start)
            if (low != high or type(start) is not int or type(end) is not int
                    or not 1 <= start <= end <= len(pages[low])):
                reasons.append("invalid_line_locator")
            else:
                selected = pages[low][start - 1:end]
        else:
            selected = [line for n in range(low, high + 1) for line in pages[n]]
    quote = citation.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        reasons.append("missing_quote")
    elif not selected or _normal(quote) not in _normal("\n".join(selected)):
        reasons.append("quote_not_at_locator")
    allowed = case.scope.get("source_ids")
    if allowed is not None and sid not in allowed:
        reasons.append("source_out_of_scope")
    bounds = case.scope.get("pages", {}).get(sid)
    if bounds and valid_pages and not bounds[0] <= low <= high <= bounds[1]:
        reasons.append("page_out_of_scope")
    result["status"] = "failed" if reasons else "passed"
    return result


def _enumeration(case: EvalCase, answer: str) -> tuple[list, list, list]:
    if case.enumeration is None:
        return [], [], []
    # A documented generic list grammar, NOT question-specific answer regexes.
    # Do not trust report.items or report.count: inspect the displayed answer.
    aliases = {_normal(form): item["id"] for item in case.enumeration
               for form in item.get("forms", [item["id"]])}
    found, extras = [], []
    for line in answer.splitlines():
        match = re.match(r"^\s*(?:[-*•]|\d+[.)、])\s+(.+?)\s*$", line)
        if match:
            text = _normal(match.group(1))
            if text in aliases:
                found.append(aliases[text])
            else:
                extras.append(text)
    counts = Counter(found)
    missing = [item["id"] for item in case.enumeration if item["id"] not in counts]
    return missing, extras, sorted(item for item, count in counts.items() if count > 1)


def _infra(report: dict, execution: ExecutionEvidence | None) -> list[str]:
    failures = set(execution.failures if execution else ())
    if execution and execution.empty_units:
        failures.add("empty_read")
    raw = report.get("errors", [])
    errors = list(raw) if isinstance(raw, list) else [raw]
    observations = report.get("observations", [])
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            if observation.get("error"):
                errors.append(observation["error"])
            value = observation.get("value")
            if isinstance(value, dict) and value.get("status") == "failed":
                errors.append({"code": "tool_failure"})
    for error in errors:
        if not error:
            continue
        text = json.dumps(error, ensure_ascii=False).lower()
        if "429" in text or "rate_limit" in text or "rate limit" in text:
            failures.add("rate_limited")
        elif "timeout" in text or "timed out" in text:
            failures.add("timeout")
        elif "empty_read" in text:
            failures.add("empty_read")
        elif any(token in text for token in ("connection", "network", "unavailable")):
            failures.add("service_unavailable")
        else:
            failures.add("reported_execution_error")
    if report.get("status") == "failed" or report.get("answer_status") == "failed":
        if not failures:
            failures.add("execution_failed")
    return sorted(failures)


def _semantic(case: EvalCase, report: dict, review: SemanticReview | None) -> tuple[str, str, list]:
    if case.label_origin == "synthetic" and case.oracle_answers:
        answer = report.get("answer")
        passed = isinstance(answer, str) and _normal(answer) in {_normal(s) for s in case.oracle_answers}
        return ("passed" if passed else "failed"), "synthetic_exact_oracle", []
    if case.label_origin != "human" or not case.annotation_id:
        return "unverified", "none", ["no_human_labels_or_synthetic_oracle"]
    if review is None:
        return "unverified", "none", ["independent_semantic_review_missing"]
    if (review.case_id != case.case_id or review.case_digest != case_digest(case)
            or review.answer_digest != answer_digest(report)):
        return "unverified", "none", ["semantic_review_binding_mismatch"]
    if (review.method not in {"human", "independent_semantic"}
            or not review.reviewer.strip() or not review.rationale.strip()
            or review.verdict not in {"passed", "failed", "unverified"}):
        return "unverified", "none", ["invalid_independent_semantic_review"]
    reasons = ["reviewer_abstained"] if review.verdict == "unverified" else []
    return review.verdict, review.method, reasons


def _number(value, integer=False):
    try:
        number = float(value or 0)
        if not math.isfinite(number) or number < 0:
            return 0
        return int(number) if integer else number
    except (TypeError, ValueError, OverflowError):
        return 0


def score_report(case: EvalCase, report: dict, *,
                 execution: ExecutionEvidence | None = None,
                 review: SemanticReview | None = None) -> EvalRow:
    """Score against independent inputs; never resolve candidate file paths."""
    _validate_case(case)
    if not isinstance(report, dict):
        report = {"errors": ["invalid_report_type"]}
    answer = report.get("answer", "")
    if not isinstance(answer, str):
        answer = ""
    text = _normal(answer)
    missing = [s for s in case.required_claims if _normal(s) not in text]
    forbidden = [s for s in case.forbidden_claims if _normal(s) in text]
    violations = []
    for rule in case.constraints:
        for s in rule.get("required_claims", []):
            if _normal(s) not in text:
                violations.append(f"{rule['id']}:missing:{s}")
        for s in rule.get("forbidden_claims", []):
            if _normal(s) in text:
                violations.append(f"{rule['id']}:forbidden:{s}")
    missing_items, extra_items, duplicates = _enumeration(case, answer)
    citations = report.get("citations", [])
    if not isinstance(citations, list):
        citations = [citations]
    checks = [_check_citation(case, c) for c in citations]
    valid_ids = {c["source_id"] for c in checks if c["status"] == "passed"}
    expected = set(case.expected_citations)
    precision = sum(c["status"] == "passed" for c in checks) / len(checks) if checks else 0.0
    recall = len(valid_ids & expected) / len(expected) if expected else 1.0
    semantic, basis, unverified = _semantic(case, report, review)
    if any(c["status"] == "unverified" for c in checks):
        unverified.append("citation_provenance_unverified")
    if expected and not case.sources:
        unverified.append("no_independent_snapshot")
    citation_failure = (any(c["status"] == "failed" for c in checks) or
                        bool(expected - valid_ids) and bool(case.sources))
    coverage = "not_required"
    missing_units = []
    if case.require_complete_coverage:
        if not case.expected_units or execution is None:
            coverage = "unverified"
            unverified.append("independent_coverage_receipts_missing")
        else:
            read_units = set(execution.read_units) - set(execution.empty_units)
            missing_units = sorted(set(case.expected_units) - read_units)
            coverage = "failed" if missing_units else "passed"
    deterministic_failure = bool(missing or forbidden or missing_items or extra_items or duplicates
                                 or violations or citation_failure or coverage == "failed")
    quality = ("failed" if deterministic_failure or semantic == "failed" else
               "passed" if semantic == "passed" and not unverified else "unverified")
    infra = _infra(report, execution)
    # An unavailable service with no answer is not a semantic wrong-answer trial.
    if infra and not text:
        quality, semantic = "unverified", "unverified"
        unverified.append("no_answer_due_to_infrastructure")
    # Missing labels/review cannot become a semantic pass OR fail merely
    # because lexical/locator diagnostics ran. Preserve those in quality_status.
    status = ("infra_failure" if infra else
              "unverified" if semantic == "unverified" else quality)
    source_validated = bool(checks) and all(c["status"] == "passed" for c in checks)
    usage = report.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    errors = report.get("errors") or []
    if not isinstance(errors, list):
        errors = [errors]
    return EvalRow(
        case_id=case.case_id, ok=status == "passed",
        grounded=source_validated and quality == "passed",
        answered=bool(text) and not (case.expect_refusal and quality == "passed"),
        expected_refusal=case.expect_refusal, citation_precision=round(precision, 3),
        citation_recall=round(recall, 3), steps=_number(report.get("steps"), True),
        tool_calls=_number(report.get("tool_calls"), True),
        elapsed_ms=_number(report.get("elapsed_ms")),
        input_tokens=_number(usage.get("prompt_tokens"), True),
        output_tokens=_number(usage.get("completion_tokens"), True),
        stop_reason=str(report.get("stop_reason") or ""), errors=errors,
        status=status, quality_status=quality, semantic_status=semantic,
        verification_basis=basis, label_origin=case.label_origin,
        source_validated=source_validated, citation_checks=checks,
        missing_claims=missing, forbidden_claims_found=forbidden,
        missing_items=missing_items, extra_items=extra_items, duplicate_items=duplicates,
        constraint_violations=violations, coverage_status=coverage,
        missing_units=missing_units, infra_failures=infra, unverified_reasons=unverified,
        budget_exceeded=_number(report.get("steps"), True) > case.max_steps,
        reported_ok=bool(report.get("ok")), reported_grounded=bool(report.get("grounded")),
        reported_answered=bool(report.get("answered")),
        reported_semantic_verified=bool(report.get("semantic_verified")))


def summarize(rows: Iterable[EvalRow]) -> dict:
    rows = list(rows)
    total = len(rows)
    counts = Counter(row.status for row in rows)
    scored = [r for r in rows if r.status in {"passed", "failed"}]
    real_scored = [r for r in scored if r.label_origin == "human"
                   and r.semantic_status in {"passed", "failed"}]
    refusal = [r for r in scored if r.expected_refusal]
    denominator = max(total, 1)
    return {
        "total": total,
        "accuracy": round(counts["passed"] / denominator, 3),
        "accuracy_definition": "independent_task_passes/all_attempted; synthetic is not real-model accuracy",
        "evaluated_accuracy": round(sum(r.ok for r in scored) / len(scored), 3) if scored else None,
        "real_semantic_accuracy": round(sum(r.ok for r in real_scored) / len(real_scored), 3) if real_scored else None,
        "real_semantic_evaluated": len(real_scored),
        "synthetic_cases": sum(r.label_origin == "synthetic" for r in rows),
        "passed": counts["passed"], "failed": counts["failed"],
        "unverified": counts["unverified"], "infra_failed": counts["infra_failure"],
        "semantic_unverified": sum(r.semantic_status == "unverified" for r in rows),
        "infra_failure_counts": dict(Counter(f for r in rows for f in set(r.infra_failures))),
        "all_passed": bool(rows) and all(r.ok for r in rows),
        "grounded_rate": round(sum(r.grounded for r in rows) / denominator, 3),
        "citation_precision": round(sum(r.citation_precision for r in rows) / denominator, 3),
        "citation_recall": round(sum(r.citation_recall for r in rows) / denominator, 3),
        "refusal_accuracy": round(sum(r.ok for r in refusal) / len(refusal), 3) if refusal else 0.0,
        "refusal_evaluated": len(refusal),
        "avg_steps": round(sum(r.steps for r in rows) / denominator, 2),
        "avg_tool_calls": round(sum(r.tool_calls for r in rows) / denominator, 2),
        "avg_elapsed_ms": round(sum(r.elapsed_ms for r in rows) / denominator, 1),
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "stop_reasons": dict(Counter(r.stop_reason for r in rows)),
    }


def evaluate(cases: Iterable[EvalCase], runner: Callable[[EvalCase], dict | RunOutcome], *,
             reviews: Mapping[str, SemanticReview] | None = None) -> dict:
    """Compatibility entry point. The caller supplies the runner explicitly.

    No provider is created here. A caller-provided runner is trusted code; for
    strictly offline use use compare_variants or this module's replay CLI.
    """
    cases = list(cases)
    if len({c.case_id for c in cases}) != len(cases):
        raise ValueError("duplicate case ids")
    rows = []
    for case in cases:
        _validate_case(case)
        try:
            outcome = runner(copy.deepcopy(case))
        except Exception as exc:
            # Do not log exception bodies, which can contain URLs/credentials.
            outcome = RunOutcome({"answer": "", "errors": [type(exc).__name__]},
                                 ExecutionEvidence(failures=("runner_exception",)))
        if isinstance(outcome, RunOutcome):
            report, execution = outcome.report, outcome.execution
        else:
            report, execution = outcome, None
        rows.append(score_report(case, report, execution=execution,
                                 review=(reviews or {}).get(case.case_id)))
    return {"rows": [asdict(row) for row in rows], "summary": summarize(rows)}


def compare_variants(cases: Iterable[EvalCase], variants: Mapping[str, dict], *,
                     reviews: Mapping[str, Mapping[str, SemanticReview]] | None = None) -> dict:
    """Score precollected A/B/C reports. Never call a model or load config.

    Each manifest supplies model_id, generation, reports, and optional execution
    receipts keyed by case id. Require identical model/settings and case sets;
    missing reports count as infrastructure failures rather than disappearing.
    Identity is asserted by the manifests, not verified against a real service.
    """
    cases = list(cases)
    if not cases or len(variants) < 2:
        raise ValueError("comparison requires cases and at least two variants")
    if len({c.case_id for c in cases}) != len(cases):
        raise ValueError("duplicate case ids")
    identities = set()
    ids = {c.case_id for c in cases}
    for name, variant in variants.items():
        if not isinstance(variant, dict) or not isinstance(variant.get("model_id"), str) or not variant["model_id"].strip():
            raise ValueError(f"variant {name} requires model_id")
        if not isinstance(variant.get("generation"), dict):
            raise ValueError(f"variant {name} requires generation settings")
        identities.add(_digest({"model_id": variant["model_id"], "generation": variant["generation"]}))
        for field_name in ("reports", "execution"):
            entries = variant.get(field_name, {})
            if not isinstance(entries, dict) or set(entries) - ids:
                raise ValueError(f"variant {name} has invalid or unknown {field_name} ids")
    if len(identities) != 1:
        raise ValueError("A/B/C comparison requires identical model and generation settings")
    results = {}
    for name, variant in variants.items():
        def replay(case):
            if case.case_id not in variant.get("reports", {}):
                return RunOutcome({"answer": ""}, ExecutionEvidence(failures=("missing_report",)))
            receipt = variant.get("execution", {}).get(case.case_id)
            execution = None
            if receipt is not None:
                execution = ExecutionEvidence(**{key: _strings(value, key) for key, value in receipt.items()})
            return RunOutcome(copy.deepcopy(variant["reports"][case.case_id]), execution)
        results[name] = evaluate(cases, replay, reviews=(reviews or {}).get(name))
    first = next(iter(variants.values()))
    return {
        "mode": "offline_precollected_comparison", "external_model_called": False,
        "model_id": first["model_id"], "generation": copy.deepcopy(first["generation"]),
        "model_identity_verified": False,
        "case_ids": [c.case_id for c in cases],
        "case_digests": {c.case_id: case_digest(c) for c in cases},
        "variants": results,
        "limitations": ["Synthetic oracle passes are not real-data or real-LLM quality measurements.",
                        "Structural/lexical checks do not prove semantic entailment.",
                        "Real cases require human labels and a bound independent semantic review.",
                        "Manifests and execution receipts must be supplied by a trusted evaluator."],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline answer-quality A/B/C replay; no application/provider imports")
    parser.add_argument("--cases", type=Path, required=True, help="Evaluator-owned synthetic/annotated case JSON")
    parser.add_argument("--reports", type=Path, required=True, help="Local JSON object with a variants mapping")
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    manifests = json.loads(args.reports.read_text(encoding="utf-8"))
    result = compare_variants(cases, manifests["variants"])
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
