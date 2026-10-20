"""Read-only meeting tools exposed to the model-driven agent loop.

All tools are application-owned. They never execute model-written code, never
accept arbitrary filesystem paths, and return untrusted content with source ids
that the citation verifier can check.
"""
from __future__ import annotations

import re

from .memory import FactStore
from .policy import injection_signals
from .sessions import SessionCatalog
from .tools import ToolContext, ToolError, ToolRegistry, ToolSpec


def _catalog(context: ToolContext) -> SessionCatalog:
    catalog = (context.extra or {}).get("catalog")
    if not isinstance(catalog, SessionCatalog):
        raise ToolError("tool_context_invalid", "session catalog is unavailable")
    return catalog


def _facts(context: ToolContext) -> FactStore:
    store = context.fact_store or (context.extra or {}).get("fact_store")
    if not isinstance(store, FactStore):
        raise ToolError("tool_context_invalid", "fact store is unavailable")
    return store


_DEFINITION = re.compile(r"记作|看成|看作|对应|等于|编码|规则|定义|分别|数字|一二三四五六")


def _read_search_sessions(args: dict, context: ToolContext) -> dict:
    catalog = _catalog(context)
    rows = catalog.search(args["query"], limit=args.get("limit", 5),
                          session_id=args.get("session_id"),
                          per_session=max(6, int(args.get("per_session", 8))))
    warnings, definition_turns = [], []
    for row in rows:
        for match in row["matches"]:
            match["text"] = str(match["text"])[:500]
            match["definition_like"] = bool(_DEFINITION.search(match["text"]))
            if match["definition_like"]:
                definition_turns.append({"session_id": row["session_id"],
                                         "turn_id": match["id"],
                                         "text": match["text"][:300]})
            signals = injection_signals(match["text"])
            if signals:
                match["injection_signals"] = len(signals)
                warnings.append(f"{row['session_id']}/{match['id']}: possible injection")
    return {"query": args["query"], "results": rows, "warnings": warnings[:8],
            "definition_turns": definition_turns[:8],
            "hint": ("优先读取 definition_turns 中的原句；规则/编码/定义类问题不能只引用主题相关片段。"
                     if definition_turns else
                     "没有命中时改用更短的关键词/同义词，并省略 session_id 以搜索全部会话")
                    if (rows or definition_turns) else
                    "没有命中时改用更短的关键词/同义词，并省略 session_id 以搜索全部会话",
            "trust": "untrusted"}


def _read_list_sessions(args: dict, context: ToolContext) -> dict:
    catalog = _catalog(context)
    rows = catalog.list_sessions(limit=args.get("limit", 10))
    for row in rows:
        try:
            session = catalog.load(row["session_id"])
            row["turns"] = len(session["turns"])
            row["clips"] = session["clips"]
        except (OSError, ValueError):
            row["turns"] = 0
            row["clips"] = 0
    return {"sessions": rows, "trust": "untrusted"}


def _read_window(args: dict, context: ToolContext) -> dict:
    catalog = _catalog(context)
    turns = catalog.window(args["session_id"], args["turn_ids"],
                           max_turns=args.get("max_turns", 12))
    if not turns:
        raise ToolError("evidence_not_found", "no requested transcript turns exist")
    warnings = []
    for turn in turns:
        turn["text"] = str(turn["text"])[:1600]
        signals = injection_signals(turn["text"])
        if signals:
            turn["injection_signals"] = len(signals)
            warnings.append(f"{turn['id']}: possible injection")
    return {"session_id": args["session_id"], "turns": turns,
            "warnings": warnings[:8], "trust": "untrusted"}


def _read_search_memory(args: dict, context: ToolContext) -> dict:
    store = _facts(context)
    rows = store.search(args["query"], limit=max(1, min(20, int(args.get("limit", 6)))))
    return {"query": args["query"], "facts": [{
        "name": fact.get("name", ""),
        "description": fact.get("description", ""),
        "type": fact.get("metadata", {}).get("type", ""),
        "origin_meeting": fact.get("metadata", {}).get("origin_meeting", ""),
        "body": str(fact.get("body", ""))[:1600],
    } for fact in rows], "trust": "project-memory"}


def _read_fact(args: dict, context: ToolContext) -> dict:
    store = _facts(context)
    try:
        fact = store.read(args["name"])
    except (FileNotFoundError, ValueError):
        raise ToolError("fact_not_found", "no such fact") from None
    return {"name": fact.get("name", ""), "description": fact.get("description", ""),
            "type": fact.get("metadata", {}).get("type", ""),
            "origin_meeting": fact.get("metadata", {}).get("origin_meeting", ""),
            "body": str(fact.get("body", ""))[:4000], "trust": "project-memory"}


def _read_compare(args: dict, context: ToolContext) -> dict:
    catalog = _catalog(context)
    result = catalog.compare(args["session_a"], args["session_b"], args["query"],
                             limit=args.get("limit", 4))
    return {**result, "trust": "untrusted"}


def _read_overview(args: dict, context: ToolContext) -> dict:
    catalog = _catalog(context)
    session = catalog.load(args["session_id"])
    turns = session["turns"][:max(1, min(30, int(args.get("max_turns", 8))))]
    return {"session_id": args["session_id"], "turns_total": len(session["turns"]),
            "clips": session["clips"],
            "speakers": sorted({turn["speaker"] for turn in session["turns"] if turn["speaker"]}),
            "first_turns": turns, "trust": "untrusted"}



def _read_project_status(args: dict, context: ToolContext) -> dict:
    """Project-progress memory: facts, focus, overview and recent meetings."""
    store = _facts(context)
    facts = store.list_facts(limit=300)
    by_type = {}
    for fact in facts:
        kind = fact.get("metadata", {}).get("type", "fact")
        by_type[kind] = by_type.get(kind, 0) + 1
    focus = []
    try:
        import json
        payload = json.loads((context.project_root / "meetingvar" / "agent_memory" / "focus.json").read_text(encoding="utf-8"))
        focus = [{"title": str(item.get("title", ""))[:160], "why": str(item.get("why", ""))[:300]}
                 for item in (payload.get("items") or [])[:10] if isinstance(item, dict)]
    except (OSError, ValueError):
        pass
    overview = ""
    try:
        import json
        payload = json.loads((context.project_root / "meetingvar" / "interest_context.json").read_text(encoding="utf-8"))
        overview = str(payload.get("overview", ""))[:500]
    except (OSError, ValueError):
        pass
    catalog = _catalog(context)
    sessions = catalog.list_sessions(limit=6)
    return {"project": str(context.project_root), "facts": len(facts), "by_type": by_type,
            "recent_facts": [{"name": f.get("name", ""),
                              "description": f.get("description", ""),
                              "type": f.get("metadata", {}).get("type", ""),
                              "origin_meeting": f.get("metadata", {}).get("origin_meeting", "")}
                             for f in facts[:10]],
            "focus": focus, "project_overview": overview,
            "recent_sessions": sessions, "trust": "project-memory"}


def _read_project_plan(args: dict, context: ToolContext) -> dict:
    """Offline reading budget for the current project; no model call."""
    from ..memory.probe import plan

    result = plan(context.project_root)
    return {"project": str(context.project_root), "ok": result.get("ok"),
            "chars": result.get("chars"), "chunks": len(result.get("chunks", [])),
            "est_local_minutes": result.get("est_local_minutes"),
            "notes": result.get("notes", [])[:12],
            "files": result.get("files", [])[:20], "error": result.get("error")}

def build_meeting_registry(*, trace=None) -> ToolRegistry:
    """Register the read-only tool surface. Write tools stay in the controlled workflow."""
    registry = ToolRegistry(trace=trace)

    registry.register(ToolSpec(
        name="search_sessions",
        description="在已保存的会议转写中按关键词检索，返回命中的会话、turn id 和原文片段。默认搜索全部会话；不要逐场重复同一查询，无结果时换更短关键词/同义词",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=10000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                "limit": {"type": "integer"},
                "per_session": {"type": "integer"},
                "session_id": {"type": "string", "maxLength": 120},
            }, "required": ["query"],
        },
    ), _read_search_sessions)

    registry.register(ToolSpec(
        name="list_sessions",
        description="列出最近保存的会议会话及转写句数、片段数",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=5000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {"limit": {"type": "integer"}}},
    ), _read_list_sessions)

    registry.register(ToolSpec(
        name="get_transcript_window",
        description="按 turn id 读取原始发言；回答中的会议引用必须来自这里",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=12000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "turn_ids": {"type": "array", "minItems": 1, "maxItems": 12,
                             "items": {"type": "string", "minLength": 1}},
                "max_turns": {"type": "integer"},
            }, "required": ["session_id", "turn_ids"],
        },
    ), _read_window)

    registry.register(ToolSpec(
        name="search_memory",
        description="检索项目事实记忆，返回事实名称、类型、来源会议和摘要",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=8000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300},
                           "limit": {"type": "integer"}},
            "required": ["query"],
        },
    ), _read_search_memory)

    registry.register(ToolSpec(
        name="read_fact",
        description="按事实名称读取完整项目记忆；回答问题引用事实时必须使用这里返回的名称",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=6000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 300}},
            "required": ["name"],
        },
    ), _read_fact)

    registry.register(ToolSpec(
        name="compare_sessions",
        description="在同一查询下对比两个会议的原文命中，用于发现承诺/决定的前后变化",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=10000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "session_a": {"type": "string", "minLength": 1, "maxLength": 120},
                "session_b": {"type": "string", "minLength": 1, "maxLength": 120},
                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                "limit": {"type": "integer"},
            }, "required": ["session_a", "session_b", "query"],
        },
    ), _read_compare)

    registry.register(ToolSpec(
        name="get_session_overview",
        description="读取会话的句数、片段数、说话人和开头几轮，用于定位要读哪一场",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=8000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"session_id": {"type": "string", "minLength": 1, "maxLength": 120},
                           "max_turns": {"type": "integer"}},
            "required": ["session_id"],
        },
    ), _read_overview)


    registry.register(ToolSpec(
        name="project_status",
        description="读取当前项目的进度记忆：事实、会前关注、项目概览和最近会议",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=9000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {}},
    ), _read_project_status)

    registry.register(ToolSpec(
        name="plan_project_reading",
        description="离线计算当前项目全库阅读的字数、块数和预计耗时，不调用模型",
        read_only=True, side_effect_scope="none", timeout_s=30, max_output_chars=8000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {}},
    ), _read_project_plan)

    return registry