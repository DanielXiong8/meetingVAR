"""Project context for semantic capture; confirmed terms never become triggers."""
from __future__ import annotations

import json
from pathlib import Path


def context_from_result(result: dict) -> dict:
    terms = []
    for item in result.get("terms", [])[:40]:
        if isinstance(item, dict) and isinstance(item.get("term"), str):
            terms.append({"term": item["term"][:120], "why": str(item.get("why", ""))[:200]})
    return {"version": 1, "overview": str(result.get("overview", ""))[:1000],
            "terms": terms, "coverage_pct": result.get("coverage_pct"),
            "notice": "这是模型对已读取材料的摘要，覆盖率按读取器口径计算；实体出现不构成保存理由。"}


def project_context(project: Path, confirmed_terms: list[str]) -> str:
    data = {"project": project.name, "confirmed_terms": confirmed_terms[:200],
            "scope": "确认专业词与已保存的模型阅读摘要；未提供的材料不能假定已阅读"}
    path = project / "meetingvar" / "interest_context.json"
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as stream:
                saved = json.loads(stream.read(100000))
            if isinstance(saved, dict):
                data["model_reading"] = context_from_result(saved)
        except (OSError, ValueError):
            data["context_warning"] = "项目阅读摘要不可用"
    focus_path = project / "meetingvar" / "agent_memory" / "focus.json"
    if focus_path.is_file():
        try:
            with focus_path.open(encoding="utf-8") as stream:
                focus = json.loads(stream.read(20000))
            items = focus.get("items") if isinstance(focus, dict) else None
            if isinstance(items, list):
                data["focus_items"] = [
                    {"title": str(item.get("title", ""))[:200],
                     "why": str(item.get("why", ""))[:500]}
                    for item in items[:8] if isinstance(item, dict)
                ]
        except (OSError, ValueError):
            data["focus_warning"] = "会前关注清单不可用"
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= 16000:
        return text
    # Keep focus_items intact before falling back to a smaller model reading.
    reading = data.get("model_reading")
    if isinstance(reading, dict):
        data["model_reading"] = {
            "overview": str(reading.get("overview", ""))[:500],
            "terms": (reading.get("terms") or [])[:10],
            "truncated": True,
        }
        text = json.dumps(data, ensure_ascii=False)
    if len(text) > 16000:
        data.pop("model_reading", None)
        data["context_truncated"] = True
        text = json.dumps(data, ensure_ascii=False)
    return text[:16000]
