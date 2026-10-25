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
    rows = store.search(
        args["query"], limit=max(1, min(20, int(args.get("limit", 6)))),
        types=args.get("types") or None,
        status=str(args.get("status") or "active"),
    )
    return {"query": args["query"], "facts": [{
        "name": fact.get("name", ""),
        "description": fact.get("description", ""),
        "type": fact.get("metadata", {}).get("type", ""),
        "status": fact.get("metadata", {}).get("status", "active"),
        "origin_meeting": fact.get("metadata", {}).get("origin_meeting", ""),
        "version": fact.get("metadata", {}).get("version", 1),
        "updated_at": fact.get("metadata", {}).get("updated_at", ""),
        "score": fact.get("_score", 0),
        "why": fact.get("_why", []),
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
            "status": fact.get("metadata", {}).get("status", "active"),
            "version": fact.get("metadata", {}).get("version", 1),
            "origin_meeting": fact.get("metadata", {}).get("origin_meeting", ""),
            "updated_at": fact.get("metadata", {}).get("updated_at", ""),
            "body": str(fact.get("body", ""))[:4000], "trust": "project-memory"}


def _read_search_conversations(args: dict, context: ToolContext) -> dict:
    from .chat import ChatStore

    store = ChatStore(context.project_root)
    rows = store.search_conversations(
        args["query"], limit=max(1, min(20, int(args.get("limit", 5)))))
    return {"query": args["query"], "conversations": rows,
            "trust": "local-conversation"}


def _read_conversation(args: dict, context: ToolContext) -> dict:
    from .chat import ChatError, ChatStore

    store = ChatStore(context.project_root)
    try:
        data = store.read_conversation(
            args["conversation_id"],
            max_messages=max(1, min(60, int(args.get("max_messages", 20)))))
    except (ChatError, OSError, ValueError):
        raise ToolError("conversation_not_found", "no such conversation") from None
    return {"conversation": data, "trust": "local-conversation"}


def _rag(context: ToolContext):
    from ..rag import RagIndex

    catalog = (context.extra or {}).get("catalog")
    data_dir = catalog.pool.parent if isinstance(catalog, SessionCatalog) else None
    return RagIndex(context.project_root, data_dir)


def _read_search_knowledge(args: dict, context: ToolContext) -> dict:
    index = _rag(context)
    chunks = index.search(
        args["query"], limit=max(1, min(20, int(args.get("limit", 6)))),
        source_type=args.get("source_type") or None, rerank=True)
    return {"query": args["query"], "chunks": chunks, "trust": "project-rag"}


def _read_project_chunk(args: dict, context: ToolContext) -> dict:
    chunk = _rag(context).read_chunk(args["chunk_id"])
    if not chunk:
        raise ToolError("chunk_not_found", "no such RAG chunk")
    return {"chunk": chunk, "trust": "project-rag"}


def _kg(context: ToolContext):
    from ..kg.store import GraphStore

    return GraphStore(context.project_root)


def _kg_node(store, args: dict, key: str = "node_id"):
    value = str(args.get(key) or "").strip()
    if value:
        node = store.get_node(value)
        if node:
            return node
    query = str(args.get("query") or value).strip()
    return store.resolve(query) if query else None


def _read_kg_find(args: dict, context: ToolContext) -> dict:
    store = _kg(context)
    if store.stats()["nodes"] == 0:
        return {"ok": False, "error": "graph_empty", "nodes": [],
                "hint": "先运行 meetingvar kg-build --project <项目>"}
    kind = args.get("kind") or None
    query = args["query"]
    nodes = store.find_nodes(query, kind=kind,
                             limit=max(1, min(30, int(args.get("limit", 10)))))
    exact = store.resolve(query, kind=kind)
    if exact and exact["id"] not in {node["id"] for node in nodes}:
        nodes.insert(0, exact)
    return {"ok": True, "query": query, "nodes": nodes, "trust": "project-kg"}


def _read_kg_neighbors(args: dict, context: ToolContext) -> dict:
    store = _kg(context)
    node = _kg_node(store, args)
    if not node:
        raise ToolError("node_not_found", "no matching graph node")
    result = store.neighbors(node["id"], depth=args.get("depth", 1),
                             direction=args.get("direction", "both"),
                             relation=args.get("relation") or None)
    return {**result, "trust": "project-kg"}


def _read_kg_timeline(args: dict, context: ToolContext) -> dict:
    store = _kg(context)
    node = _kg_node(store, args)
    if not node:
        raise ToolError("node_not_found", "no matching graph node")
    return {**store.timeline(node["id"]), "trust": "project-kg"}


def _read_kg_path(args: dict, context: ToolContext) -> dict:
    store = _kg(context)
    src = _kg_node(store, args, "src_id")
    dst = _kg_node(store, args, "dst_id")
    if not src or not dst:
        raise ToolError("node_not_found", "source or destination node is missing")
    result = store.path(src["id"], dst["id"],
                        max_depth=args.get("max_depth", 3),
                        relation=args.get("relation") or None)
    return {**result, "src": src, "dst": dst, "trust": "project-kg"}


def _read_graph_search(args: dict, context: ToolContext) -> dict:
    from ..kg.graphrag import GraphRAG

    catalog = (context.extra or {}).get("catalog")
    data_dir = catalog.pool.parent if isinstance(catalog, SessionCatalog) else None
    result = GraphRAG(context.project_root, data_dir).search(
        args["query"], limit=max(1, min(20, int(args.get("limit", 8)))),
        hops=max(1, min(3, int(args.get("hops", 2)))),
        as_of=str(args.get("as_of") or ""))
    return {**result, "trust": "project-kg-rag"}


def _read_kg_stats(args: dict, context: ToolContext) -> dict:
    return {**_kg(context).stats(), "trust": "project-kg"}


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
    all_facts = store.list_facts(limit=1000, status="all", include_inactive=True)
    facts = [fact for fact in all_facts if fact.get("status", "active") == "active"]
    by_type, by_status = {}, {}
    for fact in all_facts:
        kind = fact.get("metadata", {}).get("type", "fact")
        by_type[kind] = by_type.get(kind, 0) + 1
        state = fact.get("status", "active")
        by_status[state] = by_status.get(state, 0) + 1
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
    pending = [fact for fact in all_facts if fact.get("status") == "pending-conflict"]
    return {"project": str(context.project_root), "facts": len(facts), "by_type": by_type,
            "by_status": by_status, "pending_conflicts": len(pending),
            "recent_facts": [{"name": f.get("name", ""),
                              "description": f.get("description", ""),
                              "type": f.get("metadata", {}).get("type", ""),
                              "status": f.get("status", "active"),
                              "version": f.get("metadata", {}).get("version", 1),
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


def _propose_focus(args: dict, context: ToolContext) -> dict:
    from .focus import FocusAgent

    agent = FocusAgent(project_root=context.project_root, dry_run=True,
                       approval=lambda items: True)
    report = agent.run()
    if not report.get("ok") or not report.get("items"):
        return {"ok": False, "error": report.get("error") or "没有生成关注点",
                "error_code": report.get("error_code") or "focus_failed"}
    return {"ui_action": "save_focus", "label": f"保存 {len(report['items'])} 条会前关注",
            "payload": {"items": report["items"], "sources": report.get("sources", [])},
            "summary": "会前关注清单已生成，确认后写入项目记忆。"}


def _propose_facts(args: dict, context: ToolContext) -> dict:
    import json
    from .. import config
    from .postmeeting import PostMeetingAgent, load_transcript

    session_id = str(args.get("session_id") or "")
    catalog = _catalog(context)
    if not session_id:
        for row in catalog.list_sessions(limit=20):
            try:
                if catalog.load(row["session_id"])["turns"]:
                    session_id = row["session_id"]
                    break
            except (OSError, ValueError):
                continue
    if not session_id:
        return {"ok": False, "error": "没有可提取事实的会议", "error_code": "no_session"}
    path = Path(catalog.pool) / session_id / "transcript.json"
    try:
        transcript = load_transcript(path)
    except (OSError, ValueError):
        return {"ok": False, "error": "该会话没有可读转写", "error_code": "no_transcript"}
    agent = PostMeetingAgent(session_id=session_id, transcript=transcript,
                             project_root=context.project_root, dry_run=True,
                             approval=lambda item, evidence: True)
    report = agent.preview()
    if not report.get("ok"):
        return {"ok": False, "error": report.get("error") or "事实提取失败",
                "error_code": report.get("error_code") or "facts_failed"}
    if not report.get("items"):
        return {"ok": False, "error": "这次会议没有可写入的明确事实", "error_code": "no_facts"}
    return {"ui_action": "save_meeting_facts",
            "label": f"写入 {len(report['items'])} 条会议事实",
            "payload": {"session_id": session_id, "items": report["items"]},
            "summary": f"{session_id} 已提取 {len(report['items'])} 条候选事实。"}


def _propose_meeting(args: dict, context: ToolContext) -> dict:
    return {"ui_action": "start_meeting", "label": "开始会议",
            "payload": {"project": str(context.project_root)},
            "summary": "请求开始会议；需要你在对话里确认。"}


def _propose_stop(args: dict, context: ToolContext) -> dict:
    return {"ui_action": "stop_meeting", "label": "结束会议",
            "payload": {}, "summary": "请求结束会议；需要你在对话里确认。"}


def _propose_reading(args: dict, context: ToolContext) -> dict:
    return {"ui_action": "start_project_reading", "label": "开始全库理解",
            "payload": {"project": str(context.project_root)},
            "summary": "请求开始全库理解；需要你在对话里确认。"}


def _read_project_terms(args: dict, context: ToolContext) -> dict:
    import json

    terms = []
    try:
        payload = json.loads((context.project_root / "meetingvar" / "interest_context.json").read_text(encoding="utf-8"))
        for item in (payload.get("terms") or [])[:80]:
            if isinstance(item, dict) and item.get("term"):
                terms.append({"term": str(item["term"])[:80],
                              "why": str(item.get("why", ""))[:200],
                              "count": item.get("count", 0)})
    except (OSError, ValueError):
        pass
    existing = []
    try:
        for line in (context.project_root / "meetingvar" / "terms.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("#!keep "):
                existing.append(line[len("#!keep "):].strip())
    except OSError:
        pass
    return {"candidates": terms, "confirmed_terms": existing,
            "hint": "这些是全库理解候选；确认后用 save_project_terms 写入 #!keep 词表"}


def _propose_terms(args: dict, context: ToolContext) -> dict:
    terms = args.get("terms")
    if not isinstance(terms, list) or not terms:
        return {"ok": False, "error": "没有选择要写入的词", "error_code": "empty_terms"}
    clean = []
    for value in terms[:80]:
        if isinstance(value, str) and value.strip():
            clean.append(value.strip()[:80])
    if not clean:
        return {"ok": False, "error": "没有有效词", "error_code": "empty_terms"}
    return {"ui_action": "save_terms", "label": f"写入 {len(clean)} 个确认词",
            "payload": {"terms": clean},
            "summary": "确认后把词合并写入项目 #!keep 词表。"}

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
                           "limit": {"type": "integer"},
                           "types": {"type": "array", "items": {"type": "string"}},
                           "status": {"type": "string"}},
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
        name="search_conversations",
        description="检索历史对话的标题、滚动摘要和消息片段，用于跨会话回忆",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=8000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300},
                           "limit": {"type": "integer"}},
            "required": ["query"],
        },
    ), _read_search_conversations)

    registry.register(ToolSpec(
        name="read_conversation",
        description="读取某个历史对话的摘要和最近消息",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=12000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"conversation_id": {"type": "string", "minLength": 1, "maxLength": 64},
                           "max_messages": {"type": "integer"}},
            "required": ["conversation_id"],
        },
    ), _read_conversation)

    registry.register(ToolSpec(
        name="search_project_knowledge",
        description="检索项目文档和已保存会议转写的 RAG 知识库，返回带来源/时间戳的片段",
        read_only=True, side_effect_scope="none", timeout_s=30, max_output_chars=12000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
                           "source_type": {"type": "string"},
                           "limit": {"type": "integer"}},
            "required": ["query"],
        },
    ), _read_search_knowledge)

    registry.register(ToolSpec(
        name="read_project_chunk",
        description="按 chunk_id 读取 RAG 片段原文与来源",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=8000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"chunk_id": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["chunk_id"],
        },
    ), _read_project_chunk)

    registry.register(ToolSpec(
        name="kg_find_entities",
        description="在项目知识图谱中查找人物/项目/决定/任务/风险等实体",
        read_only=True, side_effect_scope="none", timeout_s=15, max_output_chars=8000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300},
                           "kind": {"type": "string"},
                           "limit": {"type": "integer"}},
            "required": ["query"],
        },
    ), _read_kg_find)

    registry.register(ToolSpec(
        name="kg_neighbors",
        description="查看知识图谱中某实体的邻居（支持关系/方向/深度）",
        read_only=True, side_effect_scope="none", timeout_s=15, max_output_chars=12000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"node_id": {"type": "string"},
                           "query": {"type": "string"},
                           "depth": {"type": "integer"},
                           "direction": {"type": "string"},
                           "relation": {"type": "string"}},
            "required": [],
        },
    ), _read_kg_neighbors)

    registry.register(ToolSpec(
        name="kg_timeline",
        description="查看某实体的时间线（关系、有效期、证据）",
        read_only=True, side_effect_scope="none", timeout_s=15, max_output_chars=10000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"node_id": {"type": "string"},
                           "query": {"type": "string"}},
            "required": [],
        },
    ), _read_kg_timeline)

    registry.register(ToolSpec(
        name="kg_path",
        description="查找两个实体之间的最短关系路径",
        read_only=True, side_effect_scope="none", timeout_s=20, max_output_chars=10000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"src_id": {"type": "string"}, "dst_id": {"type": "string"},
                           "query": {"type": "string"},
                           "max_depth": {"type": "integer"},
                           "relation": {"type": "string"}},
            "required": [],
        },
    ), _read_kg_path)

    registry.register(ToolSpec(
        name="search_graph",
        description="GraphRAG：实体/关系扩展 + 项目文档/会议片段，返回图关系、证据和 chunk",
        read_only=True, side_effect_scope="none", timeout_s=45, max_output_chars=16000,
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
                           "limit": {"type": "integer"},
                           "hops": {"type": "integer"},
                           "as_of": {"type": "string"}},
            "required": ["query"],
        },
    ), _read_graph_search)

    registry.register(ToolSpec(
        name="kg_stats",
        description="项目知识图谱规模与类型统计",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=3000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {}},
    ), _read_kg_stats)

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


    registry.register(ToolSpec(
        name="prepare_focus",
        description="读取项目材料并生成会前关注清单候选；不会直接写入，确认后在对话卡片里保存",
        read_only=True, side_effect_scope="none", timeout_s=180, max_output_chars=5000,
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    ), _propose_focus)

    registry.register(ToolSpec(
        name="prepare_meeting_facts",
        description="从最近或指定会议提取行动项/决定/承诺/风险/未决问题候选；不会直接写入，确认后在对话卡片里保存",
        read_only=True, side_effect_scope="none", timeout_s=180, max_output_chars=8000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {"session_id": {"type": "string", "maxLength": 120}}},
    ), _propose_facts)

    registry.register(ToolSpec(
        name="start_meeting",
        description="请求开始会议；只产生待确认操作，不会自己录音",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=1000,
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    ), _propose_meeting)

    registry.register(ToolSpec(
        name="stop_meeting",
        description="请求结束当前会议；只产生待确认操作",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=1000,
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    ), _propose_stop)

    registry.register(ToolSpec(
        name="start_project_reading",
        description="请求开始项目全库理解；只产生待确认操作，不直接写词表",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=1000,
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    ), _propose_reading)


    registry.register(ToolSpec(
        name="get_project_terms",
        description="读取项目全库理解候选词和已确认词表；不写文件",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=8000,
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    ), _read_project_terms)

    registry.register(ToolSpec(
        name="save_project_terms",
        description="请求把确认词合并写入项目 #!keep 词表；只产生待确认操作",
        read_only=True, side_effect_scope="none", timeout_s=10, max_output_chars=1000,
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {"terms": {"type": "array", "minItems": 1, "maxItems": 80,
                                                "items": {"type": "string", "minLength": 1,
                                                          "maxLength": 80}}},
                      "required": ["terms"]},
    ), _propose_terms)

    return registry