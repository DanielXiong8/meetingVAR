"""Project-scoped, auditable L2 threshold calibration from explicit reviews.

This is a conservative heuristic, not a trained model: each reviewed L2 clip
moves the threshold by a small bounded step. L1/L3 reviews are deliberately
excluded because they do not carry an interest-similarity score.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
import time
from pathlib import Path

from .. import config

MIN_THRESHOLD = 0.05
MAX_THRESHOLD = 0.95
THRESHOLD_STEP = 0.02
SCORE_MARGIN = 0.01
_SCORE_RE = re.compile(r"interest_score\s*=\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def calibration_path(project_root: Path) -> Path:
    return project_root / "meetingvar" / "calibration.json"


def _score_from_clip(clip: dict) -> float | None:
    if clip.get("trigger", {}).get("layer") != "L2":
        return None
    match = _SCORE_RE.search(str(clip.get("trigger", {}).get("evidence", "")))
    if not match:
        return None
    score = float(match.group(1))
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


def load_threshold(project_root: Path | None, default: float | None = None) -> float:
    """Load a project's calibrated threshold, falling back to the global default."""
    fallback = config.L2_SIM_THRESHOLD if default is None else default
    if project_root is None:
        return fallback
    path = calibration_path(project_root)
    try:
        value = float(json.loads(path.read_text(encoding="utf-8"))["l2_threshold"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return fallback
    return value if math.isfinite(value) and MIN_THRESHOLD <= value <= MAX_THRESHOLD else fallback


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temp_name = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temp_name).replace(path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def apply_reviews(project_root: Path, clips: list[dict]) -> dict:
    """Upsert reviewed L2 clips and recompute a deterministic bounded threshold."""
    path = calibration_path(project_root)
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        prior = {}
    base = prior.get("base_threshold", config.L2_SIM_THRESHOLD)
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = config.L2_SIM_THRESHOLD
    if not math.isfinite(base):
        base = config.L2_SIM_THRESHOLD
    base = min(MAX_THRESHOLD, max(MIN_THRESHOLD, base))

    samples_by_id = {
        sample["clip_id"]: sample
        for sample in prior.get("samples", [])
        if isinstance(sample, dict) and sample.get("clip_id")
    }
    added_or_updated = 0
    for clip in clips:
        review = clip.get("review", {})
        score = _score_from_clip(clip)
        status = review.get("status")
        if score is None or status not in {"useful", "useless"}:
            continue
        clip_id = str(clip.get("clip_id", ""))
        if not clip_id:
            continue
        sample = {
            "clip_id": clip_id,
            "session_id": clip.get("session_id"),
            "score": score,
            "status": status,
            "reviewed_at": clip.get("created_at", ""),
        }
        if samples_by_id.get(clip_id) != sample:
            added_or_updated += 1
            samples_by_id[clip_id] = sample

    samples = sorted(samples_by_id.values(), key=lambda item: (item.get("reviewed_at", ""), item["clip_id"]))
    threshold = base
    for sample in samples:
        score = float(sample["score"])
        if sample["status"] == "useful":
            threshold = min(threshold - THRESHOLD_STEP, score - SCORE_MARGIN)
        else:
            threshold = max(threshold + THRESHOLD_STEP, score + SCORE_MARGIN)
        threshold = min(MAX_THRESHOLD, max(MIN_THRESHOLD, threshold))

    payload = {
        "version": 1,
        "base_threshold": base,
        "l2_threshold": round(threshold, 4),
        "sample_count": len(samples),
        "by_status": {
            status: sum(1 for sample in samples if sample["status"] == status)
            for status in ("useful", "useless")
        },
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "Each explicit L2 useful review lowers the threshold; each useless review raises it above that example's score. Steps and range are bounded; L1/L3 are excluded.",
        "samples": samples,
    }
    _atomic_write(path, payload)
    return {**payload, "path": str(path), "new_samples": added_or_updated}
