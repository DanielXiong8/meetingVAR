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

_SAFE_ID = re.compile(r"[0-9a-f]{32}")
_GREETING = re.compile(r"^(你好|您好|hi|hello|在吗|早上好|下午好|晚上好|谢谢|好的|ok)[!！。.\s]*$", re.I)
_SUMMARY = re.compile(r"总结|纪要|复盘|主要决定|行动项|未决问题|决议", re.I)
_MEETING = re.compile(
    r"会议|项目|纪要|交付|决定|承诺|风险|进度|任务|上次|哪次|谁|什么时候|讨论|记录|"
    r"证据|转写|关注|事实|记忆|检索|总结|复盘|安排|负责|截止|方案|评审|结论|行动项|"
    r"课堂|老师|同学|上课|讲座|原话|记作|看成|提到|定义|编码|公式|符号|概念|"
    r"第一版|初版|改口|变动|冲突", re.I)


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
                   "created_at": now, "updated_at": now, "messages": [], "pending_actions": []}
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
        return [{"role": m["role"], "content": m.get("content", "")}
                for m in self.conversation.get("messages", [])[-10:]
                if m.get("role") in {"user", "assistant"} and m.get("content")]

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

    def execute_action(self, action_id: str) -> dict:
        action = self.store.action(self.conversation_id, action_id)
        if not action:
            return {"ok": False, "error": "操作不存在"}
        if action.get("status") != "pending":
            return {"ok": False, "error": "该操作已处理"}
        kind, payload = action.get("type"), action.get("payload") or {}
        result = {}
        try:
            if kind in {"start_meeting", "stop_meeting", "start_project_reading"}:
                result = {"ok": True, "ui_only": True, "action": kind}
                status = "confirmed"
                text = {"start_meeting": "已确认开始会议。",
                        "stop_meeting": "已确认结束会议。",
                        "start_project_reading": "已确认开始全库理解。"}[kind]
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
                path = Path(config.POOL_DIR) / session_id / "transcript.json"
                transcript = load_transcript(path)
                agent = PostMeetingAgent(session_id=session_id, transcript=transcript,
                                         project_root=self.project_root, dry_run=False,
                                         approval=lambda item, evidence: True)
                result = agent.save_items(payload.get("items") or [])
                status = "done" if result.get("ok") else "failed"
                text = (f"已写入 {result.get('written', 0)} 条会议事实。"
                        if result.get("ok") else "会议事实写入失败。")
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
        self.conversation = self.store.append(self.conversation_id, assistant)
        return {"ok": bool(result.get("ok")), "ui_only": bool(result.get("ui_only")),
                "action": kind, "result": result, "message": assistant,
                "conversation_id": self.conversation_id}

    def ask(self, message: str) -> dict:
        text = str(message or "").strip()
        if not text:
            raise ChatError("empty_message", "请输入消息")
        history = self._history()
        self.store.append(self.conversation_id, {"role": "user", "content": text})

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
        elif _MEETING.search(text):
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
        return {"ok": True, "conversation_id": self.conversation_id,
                "title": self.conversation.get("title", "新对话"),
                "message": assistant, "conversation": self.conversation}