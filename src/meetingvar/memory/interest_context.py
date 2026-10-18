"""Project context for semantic capture; confirmed terms never become triggers."""
from __future__ import annotations

import json
from pathlib import Path


def context_from_result(result: dict) -> dict:
    terms = []
    for item in result.get("terms", [])[:100]:
        if isinstance(item, dict) and isinstance(item.get("term"), str):
            terms.append({"term": item["term"][:120], "why": str(item.get("why", ""))[:300]})
    return {"version": 1, "overview": str(result.get("overview", ""))[:2000],
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
    return json.dumps(data, ensure_ascii=False)[:16000]
