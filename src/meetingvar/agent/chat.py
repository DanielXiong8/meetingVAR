"""Conversation-first chat for meetingVAR.

A conversation is a project-scoped JSON thread. Meeting questions run through
the bounded AgentLoop (tools + citations); ordinary chat uses the selected model
without tools. The store is intentionally local and content-minimised.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

from .. import config
from ..llm import chat_result
from .digest_skill import MeetingDigestSkill
from .loop import AgentLoop
from .memory_intent import is_memory_request, propose_memory

_SAFE_ID = re.compile(r"[0-9a-f]{32}")
_GREETING = re.compile(r"^(你好|您好|hi|hello|在吗|早上好|下午好|晚上好|谢谢|好的|ok)[!！。.\s]*$", re.I)
_SUMMARY = re.compile(r"总结|纪要|复盘|主要决定|行动项|未决问题|决议", re.I)
_KNOWLEDGE = re.compile(r"文件|文档|资料|规范|知识库|说明书|手册|需求|RAG", re.I)
_MEETING = re.compile(
    r"会议|项目|纪要|交付|决定|承诺|风险|进度|任务|上次|哪次|谁|什么时候|讨论|记录|"
    r"证据|转写|关注|事实|记忆|检索|总结|复盘|安排|负责|截止|方案|评审|结论|行动项|"
    r"课堂|老师|同学|上课|讲座|原话|记作|看成|提到|定义|编码|公式|符号|概念|"
    r"第一版|初版|改口|变动|冲突", re.I)


def _query_terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]+|[一-鿿]{2,}", str(text or ""))
    terms = []
    for word in words:
        folded = word.casefold()
        terms.append(folded)
        if re.fullmatch(r"[一-鿿]+", word):
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


class ChatError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".chat-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


class ChatStore:
    def __init__(self, project_root: Path):
        self.root = Path(project_root) / "meetingvar" / "agent_memory" / "conversations"

    def _path(self, conversation_id: str) -> Path:
        if not _SAFE_ID.fullmatch(str(conversation_id or "")):
            raise ChatError("invalid_conversation", "conversation id is invalid")
        return self.root / f"{conversation_id}.json"

    def create(self, title: str = "") -> dict:
        conversation_id = uuid.uuid4().hex
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        payload = {"version": 1, "id": conversation_id,
                   "title": (title.strip()[:80] or "新对话"),
                   "created_at": now, "updated_at": now,
                   "summary": "", "summary_until": 0,
                   "messages": [], "pending_actions": []}
        _atomic_json(self._path(conversation_id), payload)
        return payload

    def load(self, conversation_id: str) -> dict:
        path = self._path(conversation_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ChatError("conversation_not_found", "conversation does not exist") from None
        except (OSError, ValueError):
            raise ChatError("conversation_unreadable", "conversation cannot be read") from None
        if not isinstance(payload, dict) or payload.get("id") != conversation_id:
            raise ChatError("conversation_unreadable", "conversation format is invalid")
        if not isinstance(payload.get("messages"), list):
            payload["messages"] = []
        if not isinstance(payload.get("pending_actions"), list):
            payload["pending_actions"] = []
        if not isinstance(payload.get("summary"), str):
            payload["summary"] = ""
        if not isinstance(payload.get("summary_until"), int):
            payload["summary_until"] = 0
        status = {a.get("id"): a.get("status", "pending")
                  for a in payload.get("pending_actions") or [] if isinstance(a, dict)}
        for message in payload["messages"]:
            for action in message.get("actions") or []:
                if isinstance(action, dict):
                    action["status"] = status.get(action.get("id"), "pending")
        return payload

    def append(self, conversation_id: str, message: dict) -> dict:
        payload = self.load(conversation_id)
        message = dict(message)
        message.setdefault("ts", round(time.time(), 3))
        payload["messages"].append(message)
        payload["messages"] = payload["messages"][-240:]
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if payload.get("title") in {"", "新对话"} and message.get("role") == "user":
            payload["title"] = str(message.get("content", "")).strip().replace("\n", " ")[:40] or "新对话"
        _atomic_json(self._path(conversation_id), payload)
        return payload

    def list(self, limit: int = 30) -> list[dict]:
        if not self.root.exists():
            return []
        rows = []
        for path in self.root.glob("*.json"):
            if not _SAFE_ID.fullmatch(path.stem):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            messages = payload.get("messages") or []
            preview = ""
            for message in reversed(messages):
                if message.get("role") in {"user", "assistant"} and message.get("content"):
                    preview = str(message["content"]).replace("\n", " ")[:90]
                    break
            rows.append({"id": payload.get("id"), "title": payload.get("title") or "新对话",
                         "updated_at": payload.get("updated_at", ""), "preview": preview,
                         "messages": len(messages)})
        rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
        return rows[:max(1, min(100, int(limit)))]

    def update_summary(self, conversation_id: str, summary: str,
                       until: int) -> dict:
        payload = self.load(conversation_id)
        payload["summary"] = str(summary or "")[:4000]
        payload["summary_until"] = max(0, int(until))
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_json(self._path(conversation_id), payload)
        return payload

    def search_conversations(self, query: str, limit: int = 5) -> list[dict]:
        """Small cross-conversation recall index over summaries and messages."""
        terms = _query_terms(query)
        if not terms:
            return []
        rows = []
        for path in self.root.glob("*.json"):
            if not _SAFE_ID.fullmatch(path.stem):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            messages = payload.get("messages") or []
            haystack = "\n".join(
                [str(payload.get("title", "")), str(payload.get("summary", ""))]
                + [str(m.get("content", "")) for m in messages if m.get("role") in {"user", "assistant"}])
            folded = haystack.casefold()
            score = sum(folded.count(term) for term in terms)
            if not score:
                continue
            snippet = ""
            for message in reversed(messages):
                text = str(message.get("content", ""))
                if any(term in text.casefold() for term in terms):
                    snippet = text.replace("\n", " ")[:180]
                    break
            if not snippet and payload.get("summary"):
                snippet = str(payload["summary"])[-180:]
            rows.append({"conversation_id": path.stem,
                         "title": payload.get("title") or "新对话",
                         "updated_at": payload.get("updated_at", ""),
                         "score": score, "snippet": snippet})
        rows.sort(key=lambda row: (-row["score"], row.get("updated_at", "")), reverse=False)
        return rows[:max(1, min(20, int(limit)))]

    def read_conversation(self, conversation_id: str, max_messages: int = 30) -> dict:
        payload = self.load(conversation_id)
        return {"conversation_id": conversation_id,
                "title": payload.get("title") or "新对话",
                "summary": payload.get("summary", ""),
                "messages": payload.get("messages", [])[-max(1, min(60, int(max_messages))):]}

    def add_actions(self, conversation_id: str, actions: list[dict]) -> dict:
        payload = self.load(conversation_id)
        payload["pending_actions"] = (payload.get("pending_actions") or [])[-40:] + actions
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_json(self._path(conversation_id), payload)
        return payload

    def action(self, conversation_id: str, action_id: str) -> dict | None:
        payload = self.load(conversation_id)
        for action in payload.get("pending_actions") or []:
            if action.get("id") == action_id:
                return action
        return None

    def set_action_status(self, conversation_id: str, action_id: str, status: str,
                          result: str = "") -> dict:
        payload = self.load(conversation_id)
        for action in payload.get("pending_actions") or []:
            if action.get("id") == action_id:
                action["status"] = status
                action["result"] = result[:500]
                break
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_json(self._path(conversation_id), payload)
        return payload


class ChatAgent:
    def __init__(self, project_root: Path, conversation_id: str | None = None,
                 llm: Callable | None = None, data_dir: Path | None = None,
                 max_steps: int = 8):
        self.project_root = Path(project_root).resolve()
        self.store = ChatStore(self.project_root)
        self.llm = llm
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        raw_steps = int(max_steps)
        self.max_steps = 0 if raw_steps <= 0 else min(100, raw_steps)
        self.max_tool_calls = 0 if self.max_steps == 0 else 12
        if conversation_id:
            self.conversation = self.store.load(conversation_id)
        else:
            self.conversation = self.store.create()

    @property
    def conversation_id(self) -> str:
        return self.conversation["id"]

    def _history(self) -> list[dict]:
        rows = []
        summary = str(self.conversation.get("summary", "") or "").strip()
        if summary:
            rows.append({"role": "assistant", "content": "对话滚动摘要：\n" + summary})
        rows.extend({"role": m["role"], "content": m.get("content", "")}
                    for m in self.conversation.get("messages", [])[-8:]
                    if m.get("role") in {"user", "assistant"} and m.get("content"))
        return rows

    def _extractive_summary(self, previous: str, batch: list[dict]) -> str:
        lines = []
        for message in batch:
            text = str(message.get("content", "")).replace("\n", " ").strip()
            if not text:
                continue
            prefix = "用户" if message.get("role") == "user" else "助手"
            lines.append(f"{prefix}：{text[:140]}")
        summary = "\n".join(x for x in [previous] + lines if x)
        return summary[-1800:]

    def _llm_summary(self, previous: str, batch: list[dict]) -> str | None:
        lines = []
        for message in batch:
            text = str(message.get("content", "")).replace("\n", " ").strip()
            if text:
                prefix = "用户" if message.get("role") == "user" else "助手"
                lines.append(f"{prefix}：{text[:500]}")
        prompt = f"""把下面的项目对话压缩成一段可长期复用的记忆摘要。
保留：已确认的决定、承诺、负责人、截止时间、风险、未决问题、用户偏好；删除寒暄和重复。
不要编造；不确定的写“未确认”。不超过 500 字，只输出摘要正文。

已有摘要：
{previous or '（无）'}

新增对话：
{chr(10).join(lines)}
"""
        try:
            if self.llm is not None:
                reply = self.llm(prompt)
                if isinstance(reply, str):
                    return reply.strip()[:1800] or None
                if isinstance(reply, dict) and reply.get("ok"):
                    return str(reply.get("content", "")).strip()[:1800] or None
                return None
            reply = chat_result(prompt, temperature=0.2, timeout=30, max_tokens=600)
            if reply.get("ok"):
                return str(reply.get("content", "")).strip()[:1800] or None
        except Exception:
            return None
        return None

    def _roll_summary(self) -> None:
        """LLM semantic summary with a deterministic tail-preserving fallback."""
        messages = self.conversation.get("messages", [])
        until = int(self.conversation.get("summary_until", 0) or 0)
        if len(messages) - until < 12:
            return
        batch = messages[until:]
        previous = str(self.conversation.get("summary", "") or "").strip()
        summary = self._llm_summary(previous, batch) or self._extractive_summary(previous, batch)
        self.conversation = self.store.update_summary(
            self.conversation_id, summary, len(messages))

    def _direct_answer(self, question: str) -> str:
        if self.llm is not None:
            reply = self.llm(question)
            if isinstance(reply, str):
                return reply
            if isinstance(reply, dict) and reply.get("ok"):
                return str(reply.get("content", ""))
            raise ChatError("llm_failed", "模型调用失败")
        history = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}：{m['content'][:800]}"
            for m in self._history()[-8:])
        prompt = f"""你是 meetingVAR，一个面向复杂工作会议的个人证据 Agent。
你可以帮助用户检索会议、核对原句、查看项目记忆和讨论项目进展。回答简洁、诚实，不编造会议事实。
当前项目：{self.project_root}
最近对话：
{history or '（无）'}

用户：{question}
助手："""
        result = chat_result(prompt, temperature=0.3, timeout=120)
        if not result.get("ok"):
            raise ChatError(str(result.get("error_code") or "llm_failed"),
                            str(result.get("error") or "模型调用失败"))
        return str(result.get("content", "")).strip()

    def _refresh_kg_rules(self) -> None:
        try:
            from ..kg.build import GraphBuilder

            GraphBuilder(self.project_root, self.data_dir).build(
                include_meetings=True, reset=False)
        except Exception:
            return

    def _quick_memory_action(self) -> dict | None:
        """Find the latest saved meeting and offer model-free fact candidates."""
        try:
            from .postmeeting import PostMeetingAgent
            from .sessions import SessionCatalog

            catalog = SessionCatalog(self.data_dir)
            for row in catalog.list_sessions(limit=12):
                session_id = row["session_id"]
                session = catalog.load(session_id)
                if not session.get("turns"):
                    continue
                agent = PostMeetingAgent(session_id=session_id,
                                         transcript=session["turns"],
                                         project_root=self.project_root, dry_run=True)
                report = agent.quick_preview()
                items = report.get("items") or []
                if not items:
                    continue
                for existing in (self.conversation.get("pending_actions") or []):
                    if (existing.get("type") == "save_meeting_facts"
                            and (existing.get("payload") or {}).get("session_id") == session_id):
                        return None
                action = {
                    "id": uuid.uuid4().hex,
                    "type": "save_meeting_facts",
                    "label": f"写入本次会议 {len(items)} 条记忆",
                    "payload": {"session_id": session_id, "items": items},
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "status": "pending", "result": "",
                }
                self.store.add_actions(self.conversation_id, [action])
                return action
        except Exception:
            return None
        return None

    def postmeeting_offer(self) -> dict:
        """Create a chat thread card after a meeting ends (record button path)."""
        action = self._quick_memory_action()
        if not action:
            return {"ok": False, "error": "no_memory_candidates"}
        count = len(action["payload"].get("items") or [])
        assistant = {
            "role": "assistant",
            "content": (f"会议已结束。我从本次会议里找到 {count} 条可写入的项目记忆，"
                        "确认后才会保存；不会自动写入。"),
            "ts": round(time.time(), 3), "mode": "memory_offer",
            "actions": [{"id": action["id"], "type": action["type"],
                         "label": action["label"]}],
        }
        self.conversation = self.store.append(self.conversation_id, assistant)
        self._roll_summary()
        return {"ok": True, "conversation_id": self.conversation_id,
                "message": assistant, "conversation": self.conversation}

    def execute_action(self, action_id: str) -> dict:
        action = self.store.action(self.conversation_id, action_id)
        if not action:
            return {"ok": False, "error": "操作不存在"}
        if action.get("status") != "pending":
            return {"ok": False, "error": "该操作已处理"}
        kind, payload = action.get("type"), action.get("payload") or {}
        result = {}
        actions_out = []
        try:
            if kind in {"start_meeting", "stop_meeting", "start_project_reading"}:
                result = {"ok": True, "ui_only": True, "action": kind}
                status = "confirmed"
                text = {"start_meeting": "已确认开始会议。",
                        "stop_meeting": "已确认结束会议。",
                        "start_project_reading": "已确认开始全库理解。"}[kind]
                if kind == "stop_meeting":
                    memory_action = self._quick_memory_action()
                    if memory_action:
                        actions_out.append(memory_action)
                        text += ("\n我从本次会议里找到 "
                                 f"{len(memory_action['payload']['items'])} 条可写入的记忆，"
                                 "确认后保存到项目记忆。")
            elif kind == "save_memory":
                from .memory import FACT_TYPES, FactStore
                items = payload.get("items") if isinstance(payload.get("items"), list) else []
                store = FactStore(self.project_root)
                written, pending, errors = 0, 0, []
                for item in items[:8]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()[:200]
                    body = str(item.get("body") or "").strip()
                    if not title or not body:
                        continue
                    fact_type = str(item.get("fact_type") or "project-fact")
                    if fact_type not in FACT_TYPES:
                        fact_type = "project-fact"
                    try:
                        from .memory_judge import llm_conflicts
                        related = store.related_candidates(
                            fact_type, str(item.get("description") or title), body)
                        conflicts = llm_conflicts(item, related, self.llm or None)
                        path = store.write(
                            name=title, description=str(item.get("description") or title),
                            body=body, fact_type=fact_type,
                            source_refs=list(item.get("source_refs") or []),
                            confidence=float(item.get("confidence") or 0.9),
                            origin_meeting=str(item.get("origin_meeting") or
                                                f"chat:{self.conversation_id}"),
                            metadata={"channel": "chat"},
                            conflict_policy="auto",
                            conflict_with=conflicts or None)
                        written += 1
                        if store.read_path(path).get("status") == "pending-conflict":
                            pending += 1
                    except (OSError, ValueError, TypeError) as exc:
                        errors.append(type(exc).__name__)
                result = {"ok": written > 0, "written": written,
                          "pending_conflicts": pending, "errors": errors}
                status = "done" if written else "failed"
                text = (f"已记住 {written} 条项目记忆。"
                        if written else "没有写入记忆，请检查内容。")
                if pending:
                    text += f" 其中 {pending} 条与现有事实冲突，已放入待确认队列。"
            elif kind == "save_focus":
                from .focus import FocusAgent
                agent = FocusAgent(project_root=self.project_root, dry_run=False,
                                   approval=lambda items: True)
                result = agent.save_items(payload.get("items") or [],
                                          payload.get("sources") or [])
                status = "done" if result.get("ok") else "failed"
                text = ("关注清单已保存。" if result.get("ok")
                        else "关注清单保存失败：" + str(result.get("error") or result.get("error_code") or ""))
            elif kind == "save_meeting_facts":
                from .postmeeting import PostMeetingAgent, load_transcript
                session_id = str(payload.get("session_id") or "")
                path = Path(self.data_dir) / "pool" / session_id / "transcript.json"
                transcript = load_transcript(path)
                agent = PostMeetingAgent(session_id=session_id, transcript=transcript,
                                         project_root=self.project_root, dry_run=False,
                                         approval=lambda item, evidence: True)
                result = agent.save_items(payload.get("items") or [])
                status = "done" if result.get("ok") else "failed"
                text = (f"已写入 {result.get('written', 0)} 条会议事实。"
                        if result.get("ok") else "会议事实写入失败。")
                if result.get("pending_conflicts"):
                    text += f" 其中 {result['pending_conflicts']} 条待确认冲突。"
                if result.get("ok"):
                    self._refresh_kg_rules()
            elif kind == "save_terms":
                from ..memory.probe import write_terms
                terms = [str(t).strip() for t in (payload.get("terms") or []) if str(t).strip()]
                existing = []
                try:
                    for line in (self.project_root / "meetingvar" / "terms.txt").read_text(encoding="utf-8").splitlines():
                        if line.startswith("#!keep "):
                            existing.append(line[len("#!keep "):].strip())
                except OSError:
                    pass
                merged = list(dict.fromkeys(existing + terms))
                write_terms(self.project_root, merged)
                result = {"ok": True, "count": len(terms), "total": len(merged)}
                status = "done"
                text = f"已写入 {len(terms)} 个确认词（词表共 {len(merged)} 个）。"
            else:
                raise ChatError("unknown_action", "未知操作")
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__}
            status = "failed"
            text = "操作失败：" + type(exc).__name__
        self.store.set_action_status(self.conversation_id, action_id, status, text)
        assistant = {"role": "assistant", "content": text, "ts": round(time.time(), 3),
                     "mode": "action", "action_id": action_id, "status": status}
        if actions_out:
            assistant["actions"] = [{"id": a["id"], "type": a["type"],
                                     "label": a["label"]} for a in actions_out]
        self.conversation = self.store.append(self.conversation_id, assistant)
        self._roll_summary()
        return {"ok": bool(result.get("ok")), "ui_only": bool(result.get("ui_only")),
                "action": kind, "result": result, "message": assistant,
                "conversation_id": self.conversation_id}

    def ask(self, message: str) -> dict:
        text = str(message or "").strip()
        if not text:
            raise ChatError("empty_message", "请输入消息")
        history = self._history()
        self.store.append(self.conversation_id, {"role": "user", "content": text})

        if is_memory_request(text):
            proposals = propose_memory(text, history, self.conversation_id)
            if proposals:
                action_rows = [{
                    "id": uuid.uuid4().hex,
                    "type": "save_memory",
                    "label": "记住：" + proposals[0]["title"],
                    "payload": {"items": proposals},
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "status": "pending", "result": "",
                }]
                self.store.add_actions(self.conversation_id, action_rows)
                answer = ("我准备把这条写入项目记忆：\n"
                          f"「{proposals[0]['title']}」\n"
                          "确认后才会保存；你也可以在下方取消。")
                assistant = {"role": "assistant", "content": answer,
                             "ts": round(time.time(), 3), "mode": "memory",
                             "actions": [{"id": a["id"], "type": a["type"],
                                          "label": a["label"]} for a in action_rows]}
            else:
                answer = "要记住什么？请把内容写在“记住”后面，例如：记住：项目周五交付第一版。"
                assistant = {"role": "assistant", "content": answer,
                             "ts": round(time.time(), 3), "mode": "memory"}
            self.conversation = self.store.append(self.conversation_id, assistant)
            self._roll_summary()
            return {"ok": True, "conversation_id": self.conversation_id,
                    "title": self.conversation.get("title", "新对话"),
                    "message": assistant, "conversation": self.conversation}

        if _GREETING.fullmatch(text):
            answer = ("你好，我是 meetingVAR。可以帮你查会议原话、项目记忆和项目进度。"
                      "例如：\n• 上次说周五交付，这次谁改了口？\n• 项目现在有哪些未决问题？\n• 列出最近会议的主要决定。")
            meta = {"mode": "greeting"}
        elif _SUMMARY.search(text) and re.search(r"最近|每场|会议|这几场", text):
            digest = MeetingDigestSkill(self.project_root, self.data_dir, limit=5,
                                        llm=self.llm).run()
            answer = digest.get("answer") or "根据现有会议记录无法总结。"
            meta = {"mode": "skill", "citations": digest.get("citations", []),
                    "observations": digest.get("observations", []),
                    "usage": digest.get("usage", {}), "plan": digest.get("plan", []),
                    "grounded": digest.get("grounded"), "answered": digest.get("answered"),
                    "stop_reason": digest.get("stop_reason", "")}
        elif _MEETING.search(text) or _KNOWLEDGE.search(text):
            loop = AgentLoop(project_root=self.project_root, question=text,
                             data_dir=self.data_dir, llm=self.llm, mode="auto",
                             max_steps=self.max_steps,
                             max_tool_calls=self.max_tool_calls,
                             max_seconds=900.0 if self.max_steps == 0 else 180.0,
                             max_continuations=1, history=history)
            report = loop.run()
            answer = report.get("answer") or "根据现有证据无法回答。"
            action_rows = []
            for raw in report.get("ui_actions", []) or []:
                action_rows.append({"id": uuid.uuid4().hex, "type": raw.get("type"),
                                    "label": raw.get("label", "待确认操作"),
                                    "payload": raw.get("payload", {}),
                                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "status": "pending", "result": ""})
            if action_rows:
                self.store.add_actions(self.conversation_id, action_rows)
            meta = {"mode": "agent", "citations": report.get("citations", []),
                    "invalid_citations": report.get("invalid_citations", []),
                    "plan": report.get("plan", []),
                    "observations": report.get("observations", []),
                    "usage": report.get("usage", {}),
                    "grounded": report.get("grounded"), "answered": report.get("answered"),
                    "stop_reason": report.get("stop_reason", ""),
                    "continuations": report.get("continuations", 0),
                    "task_id": report.get("task_id", ""), "trace": report.get("trace", ""),
                    "actions": [{"id": a["id"], "type": a["type"], "label": a["label"]}
                                for a in action_rows]}
        else:
            try:
                answer = self._direct_answer(text)
                meta = {"mode": "chat"}
            except ChatError as exc:
                answer = "模型暂时无法回答：" + exc.message
                meta = {"mode": "chat", "error_code": exc.code}
        assistant = {"role": "assistant", "content": answer,
                     "ts": round(time.time(), 3), **meta}
        self.conversation = self.store.append(self.conversation_id, assistant)
        self._roll_summary()
        return {"ok": True, "conversation_id": self.conversation_id,
                "title": self.conversation.get("title", "新对话"),
                "message": assistant, "conversation": self.conversation}