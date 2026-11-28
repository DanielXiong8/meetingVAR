#!/usr/bin/env python3
"""Offline self-test for the recording-evidence skill."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_core import build_pack, parse_transcript, search, verify_pack  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT = ROOT / "examples" / "dispute-transcript.json"
ADMISSION = "他什么时候承认这批货是延迟交付的"
REFUND = "谁承诺退款"


def main() -> int:
    _meta, turns = parse_transcript(TRANSCRIPT)
    assert len(turns) >= 10, "example transcript is too small"

    admission = search(turns, ADMISSION, top=2)
    assert admission, "no segment found for the admission claim"
    top = admission[0]
    assert "我承认这批货是我们延迟交付的" in top["core_text"], (
        f"unexpected core text: {top['quote']}"
    )
    assert 180 <= top["start"] <= 240, (
        f"expected the admission context around 03:30, got {top['start_text']}"
    )
    assert top["end"] >= 340, (
        f"expected context to include the later contradiction, got {top['end_text']}"
    )
    assert top["duration"] >= 90, (
        f"full context span is too short: {int(top['duration'])}s"
    )
    assert 25 <= top["core_duration"] <= 80, (
        f"core window outside 25-80s: {int(top['core_duration'])}s"
    )
    assert top["core_start"] <= 252 <= top["core_end"], (
        f"core window misses the admission: {top['core_start_text']}-{top['core_end_text']}"
    )

    refund = search(turns, REFUND, top=2)
    assert refund, "no segment found for the refund claim"
    refund_top = refund[0]
    assert "退款我这边可以承诺" in refund_top["core_text"], (
        f"unexpected refund segment: {refund_top['quote']}"
    )
    assert refund_top["duration"] >= 90
    assert refund_top["core_start"] <= 450 <= refund_top["core_end"]

    with tempfile.TemporaryDirectory(prefix="ref_selftest_") as temporary:
        pack = build_pack(
            Path(temporary) / "pack",
            query=ADMISSION,
            transcript_path=TRANSCRIPT,
            segments=admission,
            notes=["selftest"],
            case_id="DEMO-2026-1116",
            disclaimer="selftest",
            report_name="evidence_report.md",
            excerpt_name="evidence_excerpt.txt",
        )
        result = verify_pack(pack["zip"])
        assert result["ok"], f"pack verification failed: {result['failures']}"
        manifest = json.loads(
            Path(pack["out_dir"], "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["case_id"] == "DEMO-2026-1116"
        assert manifest["claim"] == ADMISSION

    print("SELFTEST PASS")
    print(f"  admission context: {top['start_text']} - {top['end_text']} "
          f"({int(top['duration'])}s)")
    print(f"  admission core:    {top['core_start_text']} - {top['core_end_text']} "
          f"({int(top['core_duration'])}s)")
    print(f"  refund context:    {refund_top['start_text']} - {refund_top['end_text']} "
          f"({int(refund_top['duration'])}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())