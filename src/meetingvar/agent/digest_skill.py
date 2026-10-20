"""Reliable recent-meetings digest skill.

The generic ReAct loop is the wrong tool for "summarize the last N meetings":
it burns steps searching and never guarantees a digest. This skill loads each
session deterministically, extracts evidence turns, asks the model for a compact
structured summary, and formats a cited answer. It is the commercial-grade path
for the meeting-summary use case.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config
from ..llm import chat_result
from .sessions import SessionCatalog

_KEY = re.compile(r"决定|负责|截止|交付|行动|任务|问题|风险|下周|周五|周一|确认|方案|结论")
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S | re.I)


def _parse(raw: str) -> dict | None:
    text = _JSON_FENCE.sub("", str(raw or "").strip())
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class MeetingDigestSkill:
    def __init__(self, project_root: Path, data_dir: Path | None = None, limit: int = 5,
                 llm=None):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.catalog = SessionCatalog(self.data_dir)
        self.limit = max(1, min(8, int(limit)))
        self.llm = llm

    def _session_material(self, session_id: str) -> dict:
        session = self.catalog.load(session_id)
        turns = session["turns"]
        by_id = {turn["id"]: turn for turn in turns}
        chosen = []
        if turns:
            chosen.extend(turns[:4])
            chosen.extend(turns[-4:])
            chosen.extend(t for t in turns if _KEY.search(t["text"]))
            chosen = list({t["id"]: t for t in chosen}.values())[:28]
        lines = [f"[{t['id']}] {t['speaker']}: {t['text'][:260]}" for t in chosen]
        minutes = ""
        path = self.data_dir / "reports" / session_id / "minutes.md"
        try:
            minutes = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            pass
        summary = {}
        try:
            summary = json.loads((self.data_dir / "pool" / session_id / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        material = ""
        if minutes.strip():
            material += "已有薄纪要：\n" + minutes + "\n\n"
        if lines:
            material += "转写证据：\n" + "\n".join(lines)
        return {"session_id": session_id, "turns": by_id, "chosen": chosen,
                "material": material[:7000], "minutes": bool(minutes.strip()),
                "clips": session.get("clips", 0), "summary": summary}

    def _summarize_one(self, data: dict) -> tuple[dict, dict]:
        fallback = {"summary": "", "decisions": [], "actions": [], "open_questions": []}
        if not data["material"].strip():
            return fallback, {}
        prompt = f"""你是会议纪要员。只依据下面这场会议的材料提取，不要编造。
只输出 JSON：
{{"summary":"这场会议的一句话总结（≤80字）",
 "decisions":[{{"title":"决定","evidence_turns":["turn id"]}}],
 "actions":[{{"title":"行动项","owner":"明确负责人或空","due":"明确截止或空","evidence_turns":["turn id"]}}],
 "open_questions":[{{"title":"未决问题","evidence_turns":["turn id"]}}]}}
evidence_turns 必须来自本场转写中出现的 turn id；没有就填空数组。每类最多 5 条。
不要输出推理过程、代码围栏或额外字段。

会议 ID：{data['session_id']}
{data['material']}
"""
        if self.llm is not None:
            reply = self.llm(prompt)
            if isinstance(reply, str):
                reply = {"ok": True, "content": reply, "usage": {}}
        else:
            reply = chat_result(prompt, temperature=0.1, timeout=120)
        if not isinstance(reply, dict) or not reply.get("ok"):
            return fallback, {}
        parsed = _parse(reply.get("content", ""))
        if not isinstance(parsed, dict):
            return fallback, {}
        usage = reply.get("usage") or {}
        valid_ids = set(data["turns"])
        out = {"summary": str(parsed.get("summary", ""))[:200],
               "decisions": [], "actions": [], "open_questions": []}
        for key in ("decisions", "actions", "open_questions"):
            rows = parsed.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows[:5]:
                if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                    continue
                ids = [str(t) for t in row.get("evidence_turns", []) if str(t) in valid_ids][:4]
                item = {"title": row["title"].strip()[:160], "evidence_turns": ids}
                if key == "actions":
                    item["owner"] = str(row.get("owner", ""))[:80]
                    item["due"] = str(row.get("due", ""))[:80]
                out[key].append(item)
        return out, usage

    def run(self) -> dict:
        sessions = []
        for row in self.catalog.list_sessions(limit=40):
            try:
                session = self.catalog.load(row["session_id"])
            except (OSError, ValueError):
                continue
            if session.get("turns"):
                sessions.append(row)
            if len(sessions) >= self.limit:
                break
        if not sessions:
            return {"answer": "还没有可总结的会议记录。", "answered": False, "grounded": False,
                    "citations": [], "mode": "skill", "stop_reason": "no_sessions",
                    "observations": [], "usage": {}}
        blocks, citations, observations = [], [], []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
        for row in sessions:
            try:
                data = self._session_material(row["session_id"])
            except (OSError, ValueError):
                continue
            result, usage = self._summarize_one(data)
            for key in usage_total:
                usage_total[key] += int(usage.get(key) or 0)
            observations.append({"tool": "digest_session", "arguments": {
                "session_id": data["session_id"], "clips": data["clips"],
                "minutes": data["minutes"]}, "step": 0})
            if not result["summary"] and not result["decisions"] and not result["actions"] and not result["open_questions"]:
                continue
            lines = [f"**{data['session_id']}**" + (f" · {data['summary'].get('clips', data['clips'])} 片段" if data["clips"] else "")]
            if result["summary"]:
                lines.append(result["summary"])
            if result["decisions"]:
                lines.append("决定：")
                for item in result["decisions"]:
                    lines.append("• " + item["title"])
                    if item["evidence_turns"]:
                        citations.append({"kind": "turn", "session_id": data["session_id"],
                                          "turn_ids": item["evidence_turns"], "quote": ""})
            if result["actions"]:
                lines.append("行动项：")
                for item in result["actions"]:
                    suffix = "（" + " / ".join(x for x in (item.get("owner"), item.get("due")) if x) + "）" if (item.get("owner") or item.get("due")) else ""
                    lines.append("• " + item["title"] + suffix)
                    if item["evidence_turns"]:
                        citations.append({"kind": "turn", "session_id": data["session_id"],
                                          "turn_ids": item["evidence_turns"], "quote": ""})
            if result["open_questions"]:
                lines.append("未决问题：")
                for item in result["open_questions"]:
                    lines.append("• " + item["title"])
                    if item["evidence_turns"]:
                        citations.append({"kind": "turn", "session_id": data["session_id"],
                                          "turn_ids": item["evidence_turns"], "quote": ""})
            blocks.append("\n".join(lines))
        if not blocks:
            return {"answer": "最近会议没有可读的转写或纪要，无法总结。", "answered": False,
                    "grounded": False, "citations": [], "mode": "skill",
                    "stop_reason": "no_evidence", "observations": observations,
                    "usage": usage_total}
        answer = "最近会议摘要：\n\n" + "\n\n".join(blocks)
        return {"answer": answer, "answered": True, "grounded": bool(citations),
                "citations": citations[:20], "mode": "skill", "stop_reason": "completed",
                "observations": observations, "usage": usage_total,
                "plan": [{"goal": "读取最近会议并逐场提取决定/行动项/未决问题",
                          "tool": "digest_session", "why": "避免在通用循环里反复搜索"}]}