"""Conversation-first chat for meetingVAR.

A conversation is a project-scoped JSON thread. Meeting questions run through
the bounded AgentLoop (tools + citations); ordinary chat uses the selected model
without tools. The store is intentionally local and content-minimised.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .. import config
from ..llm import chat_result, chat_messages, chat_messages_stream
from ..model_client import resolve_model_client, CallableModelClient
from ..request_context import model_request
from .policy import AgentBudget
from .context import SUMMARY_TAIL, build_context
from .operations import OperationStore
from .digest_skill import MeetingDigestSkill
from .loop import AgentLoop, _clean_answer_text
from .memory_intent import is_memory_request, propose_memory
from .evidence_skills import (CommitmentLedgerSkill, EvidencePackSkill,
                              FindMomentSkill)

_SAFE_ID = re.compile(r"[0-9a-f]{32}")
_GREETING = re.compile(r"^(你好|您好|hi|hello|在吗|早上好|下午好|晚上好|谢谢|好的|ok)[!！。.\s]*$", re.I)
_SUMMARY = re.compile(r"总结|纪要|复盘|主要决定|行动项|未决问题|决议", re.I)
_EVIDENCE_PACK = re.compile(r"证据包|导出.{0,8}证据|打包.{0,8}证据|evidence\s*pack", re.I)
_COMMITMENT_LEDGER = re.compile(r"承诺账本|谁改口|改口|承诺.{0,8}变|交付.{0,8}改|截止.{0,8}改", re.I)
_FIND_MOMENT = re.compile(r"找回|定位|哪一段|哪段|30\s*秒|三十秒|原话.{0,6}哪|片段.{0,6}哪", re.I)
_FOCUS = re.compile(r"(?:生成|准备|列|做|整理).{0,8}(?:会前)?关注|(?:会前)?关注清单", re.I)
_FACTS = re.compile(r"会后事实|会议事实|提取事实|事实候选|提取.{0,8}(?:承诺|行动项|风险)", re.I)
_MEMORY_VIEW = re.compile(r"项目记忆|记忆列表|看看记忆|记忆里(?:有|存|哪些)|生效中的事实", re.I)
_RAG_INDEX = re.compile(r"(?:建立|重建|刷新).{0,8}(?:索引|知识库)|RAG\s*索引", re.I)
_RAG_ASK = re.compile(r"^(?:用|查|问)?\s*(?:项目)?(?:知识库|RAG)[:：\s]", re.I)
_SESSION_ID = re.compile(r"\b((?:go|replay)_[A-Za-z0-9_.-]+)\b")
_KNOWLEDGE = re.compile(r"文件|文档|资料|规范|知识库|说明书|手册|需求|RAG|什么位置|担任|负责|成员|名单|队员|队长|教练|战绩|比赛|活动|班级|学院|系队|球队|截止|交付|什么时候|日期|地点|哪些人|谁在|谁负责", re.I)
_SMALLTALK = re.compile(r"^(?:你好|您好|嗨|hi|hello|谢谢|你是谁|你叫什么|在吗|早上好|晚上好|好的|收到)[！!。.？?\s]*$", re.I)
_MEETING_EXPLICIT = re.compile(r"会议|纪要|转写|原话|哪次|上次|课堂|讲座|记作|看成|提到|讨论|发言|谁说过|承诺|改口|行动项|会议记录", re.I)
_FILE_INTENT = re.compile(r"读取?文件|打开文件|查看.{0,8}文件|写(?:入|到|一个)|创建.{0,8}文件|修改.{0,8}文件|编辑.{0,8}文件|保存到|README|\.md|\.txt|\.py|\.json|列出.{0,8}(?:目录|文件夹)|(?:搜索|查找|找).{0,12}(?:文件|代码|项目)|项目结构|目录结构|执行.{0,6}(?:命令|git|npm|pytest|python)|运行.{0,6}(?:命令|测试|脚本)", re.I)
_FILE_READ = re.compile(r"^\s*(?:请|帮我)?\s*(?:读取|打开|查看|read)\s+(?:文件\s*)?([^\s]+?\.[A-Za-z0-9]{1,8})\s*$", re.I)
_FILE_CAPABILITY = re.compile(
    r"(?:你能|你可以|是否|能不能|能否|会不会|可不可以).{0,30}(?:读写|读取|写入|编辑|修改|访问|读|写).{0,16}(?:文件|文档|代码|目录)"
    r"|(?:本地|电脑上|计算机上|磁盘上).{0,10}(?:文件|文档|目录|文件系统)"
    r"|(?:文件系统|目录结构).{0,8}(?:能力|权限)|(?:执行|运行).{0,10}(?:命令|终端|shell|脚本)"
    r"|文件(?:读写|读取|写入)|(?:读取|写入|编辑).{0,8}本地文件|read\s+.*file|write\s+.*file", re.I)
_FILE_WRITE = re.compile(r"^\s*(?:请|帮我)?\s*(?:写入|保存到|创建|新建|生成|write)\s+([^\s]+?\.[A-Za-z0-9]{1,8})\s*[:：]\s*(.+)$", re.S | re.I)
_DIR_LIST = re.compile(r"^\s*(?:请|帮我)?\s*(?:列出|list)\s*(?:一下)?\s*(.*?)\s*(?:目录|文件夹|文件列表|项目结构|下的文件|里的文件|文件)\s*$", re.I)
_FILE_SEARCH = re.compile(r"^\s*(?:请|帮我)?\s*(?:在(?:项目|代码|文件)(?:中|里)\s*)?(?:搜索|查找)\s*(?:文件|代码)?\s*(?:中|里)?\s*[“\"']?(.+?)[”\"']?\s*$", re.I)
_COMMAND_REQ = re.compile(r"^\s*(?:请|帮我)?\s*(?:执行|运行)\s*(?:命令|终端命令)\s*[:：]?\s*(.+)$", re.I)
_FILE_PATH = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:md|txt|py|tsx?|jsx?|json|ya?ml|csv|pdf|docx?|pptx?)", re.I)
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
    # JSON remains the transcript source; locking only serializes mutations.
    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "meetingvar" / "agent_memory" / "conversations"

    def _path(self, conversation_id: str) -> Path:
        if not _SAFE_ID.fullmatch(str(conversation_id or "")):
            raise ChatError("invalid_conversation", "conversation id is invalid")
        return self.root / f"{conversation_id}.json"

    @contextmanager
    def _locked(self, conversation_id: str):
        path = self._path(conversation_id).with_suffix(".lock")
        key = os.path.normcase(str(path.resolve()))
        with self._locks_guard:
            lock = self._locks.setdefault(key, threading.RLock())
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+b") as stream:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0, os.SEEK_END)
                    if stream.tell() == 0:
                        stream.write(b"0")
                        stream.flush()
                    deadline = time.monotonic() + 10
                    while True:
                        stream.seek(0)
                        try:
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise ChatError("conversation_busy", "conversation is busy") from None
                            time.sleep(0.01)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def create(self, title: str = "") -> dict:
        conversation_id = uuid.uuid4().hex
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        payload = {"version": 2, "id": conversation_id,
                   "title": (title.strip()[:80] or "新对话"),
                   "created_at": now, "updated_at": now,
                   "summary": "", "summary_until": 0,
                   "summary_until_seq": 0, "next_seq": 1,
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
        previous_seq = 0
        for message in payload["messages"]:
            if not isinstance(message, dict):
                raise ChatError("conversation_unreadable", "invalid message record")
            seq = message.get("seq")
            if type(seq) is not int or seq <= previous_seq:
                seq = previous_seq + 1
            message["seq"] = seq
            previous_seq = seq
        next_seq = payload.get("next_seq", 1)
        payload["next_seq"] = max(previous_seq + 1, next_seq if type(next_seq) is int else 1)
        until = payload.get("summary_until_seq")
        if type(until) is not int:
            # Old offsets may refer to an already truncated 240-message array.
            # Preserve its prose, but never claim that it covers surviving rows.
            legacy_until = payload.get("summary_until", 0)
            messages = payload["messages"]
            until = (messages[legacy_until - 1]["seq"]
                     if type(legacy_until) is int and 0 < legacy_until <= len(messages) < 240
                     else 0)
            if payload["summary"]:
                payload["summary_source"] = "legacy_unverified"
        payload["summary_until_seq"] = max(0, min(until, previous_seq))
        payload["summary_until"] = payload["summary_until_seq"]
        payload["version"] = 2
        status = {}
        operations = OperationStore(self.project_root)
        for action in payload.get("pending_actions") or []:
            if not isinstance(action, dict):
                continue
            receipt = operations.get(str(action.get("id") or ""))
            if receipt and receipt["thread_id"] == conversation_id:
                state = "done" if receipt["status"] == "succeeded" else receipt["status"]
                if state == "awaiting_approval":
                    state = "pending"
                action["status"] = state
                action["result"] = receipt.get("result")
            status[action.get("id")] = action.get("status", "pending")
        for message in payload["messages"]:
            for action in message.get("actions") or []:
                if isinstance(action, dict):
                    action["status"] = status.get(action.get("id"), "unavailable")
        return payload

    def append(self, conversation_id: str, message: dict) -> dict:
        with self._locked(conversation_id):
            payload = self.load(conversation_id)
            message = dict(message)
            message.setdefault("ts", round(time.time(), 3))
            message["seq"] = payload["next_seq"]
            payload["next_seq"] += 1
            payload["messages"].append(message)
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
                       until: int, *, expected_until: int | None = None) -> dict:
        with self._locked(conversation_id):
            payload = self.load(conversation_id)
            current = payload["summary_until_seq"]
            if expected_until is not None and current != expected_until:
                return payload
            last_seq = max((m["seq"] for m in payload["messages"]), default=0)
            target = min(max(0, int(until)), last_seq)
            if target < current:
                return payload
            payload["summary"] = str(summary or "")[:4000]
            payload["summary_until_seq"] = target
            payload["summary_until"] = target  # compatibility key, now a seq
            payload["summary_source"] = "conversation_summary_not_execution_proof"
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
        with self._locked(conversation_id):
            payload = self.load(conversation_id)
            known = {a.get("id") for a in payload["pending_actions"] if isinstance(a, dict)}
            operations = OperationStore(self.project_root)
            for action in actions:
                operations.create(str(action.get("type") or "unknown"), action.get("payload") or {},
                    operation_id=action.get("id"), thread_id=conversation_id,
                    run_id=str(action.get("run_id") or ""), turn_id=str(action.get("turn_id") or ""),
                    parent_id=str(action.get("parent_id") or ""),
                    tool_call_id=str(action.get("tool_call_id") or ""),
                    requires_approval=True, origin="chat_action")
                if action.get("id") not in known:
                    payload["pending_actions"].append(dict(action))
                    known.add(action.get("id"))
            payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            _atomic_json(self._path(conversation_id), payload)
            return payload

    def action(self, conversation_id: str, action_id: str) -> dict | None:
        payload = self.load(conversation_id)
        for action in payload.get("pending_actions") or []:
            if isinstance(action, dict) and action.get("id") == action_id:
                return action
        return None

    def set_action_status(self, conversation_id: str, action_id: str, status: str,
                          result: str | dict = "") -> dict:
        # Compatibility projection only; OperationStore owns execution truth.
        with self._locked(conversation_id):
            payload = self.load(conversation_id)
            for action in payload.get("pending_actions") or []:
                if isinstance(action, dict) and action.get("id") == action_id:
                    action["status"] = status
                    action["result"] = dict(result) if isinstance(result, dict) else str(result)[:500]
                    break
            payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            _atomic_json(self._path(conversation_id), payload)
            return payload


class ChatAgent:
    def __init__(self, project_root: Path, conversation_id: str | None = None,
                 llm: Callable | None = None, data_dir: Path | None = None,
                 max_steps: int = 8, progress: Callable | None = None,
                 model_client=None, stream: bool = False):
        self.project_root = Path(project_root).resolve()
        self.store = ChatStore(self.project_root)
        self.operations = OperationStore(self.project_root)
        self.run_id = ""
        self.turn_id = ""
        self.llm = llm
        self.model_client = resolve_model_client(model_client, complete=llm)
        # Chat's legacy callable accepts prose, while AgentLoop legacy callables
        # accept messages. Both adapters remain injected and cannot reach globals.
        if model_client is None and llm is not None:
            def legacy_complete(messages, **kwargs):
                prompt = chr(10).join(str(m["role"]) + ": " + str(m.get("content", "")) for m in messages)
                value = llm(prompt)
                return {"ok": True, "content": value} if isinstance(value, str) else value
            self._chat_client = CallableModelClient(legacy_complete)
        else:
            self._chat_client = self.model_client
        self.stream = bool(stream)
        self._request_budget = AgentBudget(max_seconds=900)
        self._last_model_result = {}
        if model_client is not None:
            self.llm = self._model_text
        self.progress = progress
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        raw_steps = int(max_steps)
        self.max_steps = 0 if raw_steps <= 0 else min(100, raw_steps)
        self.max_tool_calls = 0 if self.max_steps == 0 else 12
        if conversation_id:
            self.conversation = self.store.load(conversation_id)
        else:
            self.conversation = self.store.create()

    def _model_text(self, prompt: str, **kwargs) -> str | None:
        result = model_request(self.model_client, [{"role": "user", "content": str(prompt)}],
            current_turn=0, ledger=self._request_budget, stream=self.stream,
            temperature=kwargs.get("temperature", 0.2), timeout=kwargs.get("timeout", 120))
        self._last_model_result = result
        return str(result.get("content") or "") if result.get("ok") else None

    @property
    def conversation_id(self) -> str:
        return self.conversation["id"]

    def _context(self, *, exclude_seq: int | None = None, evidence: str = "",
                 verify_artifacts: bool = True) -> list[dict]:
        self.conversation = self.store.load(self.conversation_id)
        messages = self.conversation.get("messages", [])
        if exclude_seq is not None:
            messages = [m for m in messages if m["seq"] < exclude_seq]
        return build_context(
            self.project_root, self.conversation.get("summary", ""), messages,
            self.operations.recent_context(thread_id=self.conversation_id,
                                           limit=12, verify_artifacts=verify_artifacts),
            evidence=evidence)

    def _history(self) -> list[dict]:
        """Compatibility accessor: use the same context in both model modes."""
        return self._context()

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
保留：用户目标、讨论中的决定/承诺、负责人、截止时间、风险、未决问题、用户偏好；删除寒暄和重复。
摘要只记录谁说过什么，不验证执行。助手的“已生成/已保存”、草稿和操作提案不是成功证据；
必须区分请求、待审批、执行中、失败和真实回执；没有结构化回执的执行声明写“未核验”。
不要编造；不确定的写“未确认”。不超过 500 字，只输出摘要正文。

已有摘要：
{previous or '（无）'}

新增对话：
{chr(10).join(lines)}
"""
        reply = model_request(self._chat_client, [{"role": "user", "content": prompt}],
            current_turn=0, ledger=self._request_budget, temperature=0.2, timeout=30,
            max_tokens=min(600, self._chat_client.budget().max_output_tokens), stream=self.stream)
        if reply.get("ok"):
            return str(reply.get("content", "")).strip()[:1800] or None
        return None

    def _roll_summary(self) -> None:
        """Summarize stable seq ranges, retaining a verbatim recent tail."""
        self.conversation = self.store.load(self.conversation_id)
        messages = self.conversation.get("messages", [])
        until = self.conversation["summary_until_seq"]
        batch = [m for m in messages[:-SUMMARY_TAIL] if m["seq"] > until]
        if len(batch) < 12:
            return
        previous = str(self.conversation.get("summary", "") or "").strip()
        # Same injected client and ledger; a provider failure degrades to the
        # deterministic extractive projection instead of hiding the gap.
        summary = self._llm_summary(previous, batch) or self._extractive_summary(previous, batch)
        self.conversation = self.store.update_summary(
            self.conversation_id, summary, batch[-1]["seq"], expected_until=until)

    def _direct_answer(self, question: str, chat_mode: bool = False) -> str:
        system = ("你是 meetingVAR 的 Chat 模式，自由、自然地回答。当前轮不调用工具。"
                  "先给2-5句直接结论，再按需列要点；不要把全部背景堆成一个长段落。"
                  "可引用既有会话和历史执行回执，但不得声称本轮重新检查了文件或执行了操作。"
                  if chat_mode else "你是 meetingVAR 项目助手，只依据已提供的上下文回答。"
                  "先给2-5句直接结论，再按需列要点；不要把全部背景堆成一个长段落。")
        messages = [{"role": "system", "content": system},
                    *self._context(exclude_seq=getattr(self, "_current_seq", None), verify_artifacts=not chat_mode),
                    {"role": "user", "content": question}]
        result = model_request(self._chat_client, messages, current_turn=len(messages)-1,
            ledger=self._request_budget, temperature=0.3, timeout=120, stream=self.stream)
        self._last_model_result = result
        if not result.get("ok"):
            raise ChatError(str(result.get("error_code") or "llm_failed"),
                            str(result.get("error") or "模型调用失败"))
        answer = str(result.get("content", "")).strip()
        if callable(self.progress) and answer:
            try:
                self.progress({"phase": "answer_delta", "text": answer,
                               "thread_id": self.conversation_id, "run_id": self.run_id,
                               "turn_id": self.turn_id})
            except Exception:
                pass
        return answer

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

            catalog = SessionCatalog(self.data_dir, project_root=self.project_root)
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

    def _focus_preview(self) -> dict:
        """Pre-meeting focus proposal; the save is a chat approval action."""
        from .focus import FocusAgent

        return FocusAgent(
            project_root=self.project_root, dry_run=True,
            approval=lambda items: True, model_client=self._chat_client, model_budget=self._request_budget,
        ).preview()

    def _facts_preview(self, text: str) -> dict:
        """Post-meeting fact proposal for one transcript; save is approval-gated."""
        from .postmeeting import PostMeetingAgent
        from .sessions import SessionCatalog

        matched = _SESSION_ID.search(text)
        catalog = SessionCatalog(self.data_dir, project_root=self.project_root)
        session_id = matched.group(1) if matched else ""
        if not session_id:
            rows = catalog.list_sessions(limit=20)
            session_id = rows[0]["session_id"] if rows else ""
        if not session_id:
            return {"ok": False, "error": "没有找到有转写的会议"}
        try:
            session = catalog.load(session_id)
        except (OSError, ValueError):
            return {"ok": False, "error": "会议转写不可读"}
        if not session.get("turns"):
            return {"ok": False, "error": "这场会议没有可提取的转写"}
        report = PostMeetingAgent(
            session_id=session_id, transcript=session["turns"],
            project_root=self.project_root, dry_run=True,
            approval=lambda item, evidence: True, model_client=self._chat_client, model_budget=self._request_budget,
        ).preview()
        report["session_id"] = session_id
        return report

    def _memory_rows(self, text: str) -> tuple[list[dict], str]:
        from .memory import FactStore

        query = re.sub(
            r"(项目记忆|记忆列表|看看记忆|记忆里(?:有|存|哪些)|生效中的事实|"
            r"请|帮我|列一下|列出|查看|看下|里)", " ", text, flags=re.I,
        ).strip(" ：:，,。")
        store = FactStore(self.project_root)
        rows = (store.search(query, limit=20) if query
                else store.list_facts(limit=20, status="active"))
        return rows, query

    def _rag_index(self) -> dict:
        from ..rag import RagIndex

        return RagIndex(self.project_root, self.data_dir).index_project(
            include_meetings=True)

    def _rag_answer(self, text: str) -> dict:
        from ..rag import RagIndex

        question = re.sub(
            r"^(用|查|问)?\s*(?:项目)?(?:知识库|RAG)\s*[:：]?\s*", "",
            text, flags=re.I,
        ).strip() or text
        return RagIndex(self.project_root, self.data_dir).answer(question)

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
        kind, payload = action.get("type"), action.get("payload") or {}
        try:
            operation = self.operations.get(action_id)
            if operation is None:
                if action.get("status") != "pending":
                    return {"ok": False, "ui_only": False, "error": "旧操作结果未核验，不能重放"}
                # Migrate only an explicitly confirmed legacy pending proposal.
                operation = self.operations.create(kind, payload, operation_id=action_id,
                    thread_id=self.conversation_id, requires_approval=True, origin="legacy_pending")
            if operation["thread_id"] != self.conversation_id or operation["kind"] != kind:
                raise ValueError("操作不属于当前会话或类型不一致")
            if not self.operations.arguments_match(action_id, payload):
                raise ValueError("操作参数已变化，必须重新提案审批")
            if operation["status"] == "awaiting_approval":
                self.operations.approve(action_id)
            if kind in {"start_meeting", "stop_meeting", "start_project_reading"}:
                self.operations.claim(action_id)
            else:
                from .recovery import owner_identity
                self.operations.claim(action_id, owner=owner_identity())
            self.store.set_action_status(self.conversation_id, action_id, "running")
        except (ValueError, OSError) as exc:
            return {"ok": False, "ui_only": False, "action_id": action_id,
                    "operation_id": action_id, "error": str(exc)}
        result = {}
        actions_out = []
        try:
            if kind in {"start_meeting", "stop_meeting", "start_project_reading"}:
                text = "审批已通过，等待宿主执行并返回真实结果。"
                assistant = {"role": "assistant", "content": text, "mode": "action",
                             "action_id": action_id, "operation_id": action_id, "status": "running"}
                self.conversation = self.store.append(self.conversation_id, assistant)
                return {"ok": True, "ui_only": True, "action": kind,
                        "action_id": action_id, "operation_id": action_id, "status": "running",
                        "result": {"ok": True, "accepted": True, "ui_only": True, "action": kind},
                        "message": assistant, "conversation_id": self.conversation_id}
            elif kind == "save_evidence_pack":
                from .evidence_skills import EvidencePackSkill
                skill = EvidencePackSkill(self.project_root, self.data_dir)
                result = skill.run(
                    session_id=str(payload.get("session_id") or ""),
                    clip_id=str(payload.get("clip_id") or ""),
                    query=str(payload.get("query") or ""))
                status = "done" if result.get("ok") else "failed"
                text = ("证据包已生成：" + str(result.get("zip_path", ""))
                        if result.get("ok") else "证据包生成失败。")
            elif kind == "save_memory":
                from .memory import FACT_TYPES, FactStore
                items = payload.get("items") if isinstance(payload.get("items"), list) else []
                store = FactStore(self.project_root)
                written, pending, errors = 0, 0, []
                from .model_calls import ToolModelCalls
                conflict_model = ToolModelCalls(model_client=self._chat_client, model_budget=self._request_budget)
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
                        conflicts = llm_conflicts(item, related,
                            lambda prompt: conflict_model.complete(prompt, temperature=0, timeout=20))
                        if conflict_model.errors:
                            errors.extend(conflict_model.errors)
                            break
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
                result = conflict_model.report({"ok": written > 0 and not errors, "written": written,
                          "pending_conflicts": pending, "errors": errors})
                status = "done" if result["ok"] else "failed"
                text = (f"已记住 {written} 条项目记忆。"
                        if written else "没有写入记忆，请检查内容。")
                if pending:
                    text += f" 其中 {pending} 条与现有事实冲突，已放入待确认队列。"
            elif kind == "save_focus":
                from .focus import FocusAgent
                agent = FocusAgent(project_root=self.project_root, dry_run=False,
                                   approval=lambda items: True, model_client=self._chat_client,
                                   model_budget=self._request_budget)
                result = agent.save_items(payload.get("items") or [],
                                          payload.get("sources") or [])
                status = "done" if result.get("ok") else "failed"
                text = ("关注清单已保存。" if result.get("ok")
                        else "关注清单保存失败：" + str(result.get("error") or result.get("error_code") or ""))
            elif kind == "save_meeting_facts":
                from .postmeeting import PostMeetingAgent, load_transcript
                session_id = str(payload.get("session_id") or "")
                from ..memory.project_sessions import session_ids_for_project
                if session_id not in session_ids_for_project(self.project_root,strict=True):
                    raise ValueError("会议不属于当前项目")
                path = Path(self.data_dir) / "pool" / session_id / "transcript.json"
                transcript = load_transcript(path)
                agent = PostMeetingAgent(session_id=session_id, transcript=transcript,
                                         project_root=self.project_root, dry_run=False,
                                         approval=lambda item, evidence: True, model_client=self._chat_client,
                                         model_budget=self._request_budget)
                result = agent.save_items(payload.get("items") or [])
                status = "done" if result.get("ok") else "failed"
                text = (f"已写入 {result.get('written', 0)} 条会议事实。"
                        if result.get("ok") else "会议事实写入失败。")
                if result.get("pending_conflicts"):
                    text += f" 其中 {result['pending_conflicts']} 条待确认冲突。"

            elif kind == "run_command":
                from .file_ops import validate_command
                try:
                    command = validate_command(str(payload.get("command") or ""))
                except ValueError as exc:
                    raise ValueError(str(exc)) from None
                flags = 0x08000000 if os.name == "nt" else 0
                try:
                    proc = subprocess.run(command, shell=True, cwd=str(self.project_root),
                                          capture_output=True, text=True, timeout=120,
                                          creationflags=flags)
                    stdout = (proc.stdout or "").replace("\r\n", "\n")
                    stderr = (proc.stderr or "").replace("\r\n", "\n")
                    result = {"ok": proc.returncode == 0, "command": command,
                              "returncode": proc.returncode,
                              "stdout": stdout, "stderr": stderr}
                    status = "done" if proc.returncode == 0 else "failed"
                    text = f"命令退出码 {proc.returncode}。"
                    if stdout.strip():
                        text += "\n\n" + stdout.strip()[-3500:]
                    if stderr.strip():
                        text += "\n\n[stderr]\n" + stderr.strip()[-1500:]
                except subprocess.TimeoutExpired:
                    result = {"ok": False, "command": command, "error": "timeout"}
                    status = "failed"
                    result["outcome_unknown"] = True
                    text = "命令执行超时（120 秒）；子进程副作用未核验，不会自动重放。"
            elif kind == "write_document":
                from .document_ops import create_document
                try:
                    saved = create_document(self.project_root,
                                            str(payload.get("path") or ""),
                                            str(payload.get("content") or ""))
                    result = {"ok": True, **saved}
                    status = "done"
                    text = f"已生成 {saved['path']}（{saved['bytes']} 字节）。"
                except ValueError as exc:
                    result = {"ok": False, "error": str(exc)}
                    status = "failed"
                    text = "文档生成失败：" + str(exc)
            elif kind == "write_file":
                from .file_ops import write_text_file
                try:
                    saved = write_text_file(self.project_root,
                                            str(payload.get("path") or ""),
                                            str(payload.get("content") or ""))
                    result = {"ok": True, **saved}
                    status = "done"
                    text = f"已写入 {saved['path']}（{saved['bytes']} 字节）。"
                    if saved.get("backup"):
                        text += f" 原文件备份：{saved['backup']}"
                except ValueError as exc:
                    result = {"ok": False, "error": str(exc)}
                    status = "failed"
                    text = "文件写入失败：" + str(exc)
            elif kind == "index_project":
                from ..rag import RagIndex
                result = RagIndex(self.project_root,self.data_dir).index_project(
                    include_meetings=bool(payload.get("include_meetings",True)),force=bool(payload.get("force",False)))
                status = "done" if result.get("ok") else "failed"
                text = "知识索引已更新。" if result.get("ok") else "知识索引更新失败。"
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
        if kind in {"save_memory", "save_meeting_facts"} and result.get("ok"):
            try:
                from ..kg.build import GraphBuilder
                result["projection"] = GraphBuilder(self.project_root,self.data_dir).build(include_meetings=True,reset=False)
            except Exception:
                result.update(ok=False,mutation_applied=True,projection_pending=True,
                              error="事实已保存，但知识投影需要修复；不能重复提交事实")
                text += " 知识投影尚未同步，请勿重复提交。"
        terminal = "interrupted" if result.get("outcome_unknown") else None
        done = self.operations.finish(action_id, result, status=terminal)
        result = done["result"] or {}
        status = "done" if done["status"] == "succeeded" else done["status"]
        if done["status"] == "failed" and result.get("artifact_warnings"):
            text = "执行结果的产物核验失败；不能确认文件已生成。"
        elif done["artifacts"]:
            text += chr(10) + chr(10).join(a["absolute_path"] for a in done["artifacts"])
        self.store.set_action_status(self.conversation_id, action_id, status, result)
        assistant = {"role": "assistant", "content": text, "ts": round(time.time(), 3),
                     "mode": "action", "action_id": action_id, "operation_id": action_id,
                     "result": result, "artifacts": done["artifacts"], "status": status}
        if actions_out:
            assistant["actions"] = [{"id": a["id"], "type": a["type"],
                                     "label": a["label"]} for a in actions_out]
        self.conversation = self.store.append(self.conversation_id, assistant)
        self._roll_summary()
        return {"ok": bool(result.get("ok")), "ui_only": bool(result.get("ui_only")),
                "action": kind, "action_id": action_id, "operation_id": action_id,
                "status": done["status"], "artifacts": done["artifacts"],
                "result": result, "message": assistant,
                "conversation_id": self.conversation_id}

    def finalize_action(self, action_id: str, result: dict) -> dict:
        """Trusted host receipt. Approval and execution are different states."""
        action = self.store.action(self.conversation_id, action_id)
        operation = self.operations.get(action_id)
        if (not action or not operation or operation["thread_id"] != self.conversation_id
                or operation["kind"] != action.get("type")
                or operation["kind"] not in {"start_meeting", "stop_meeting", "start_project_reading"}):
            return {"ok": False, "error": "回执不属于当前会话的宿主操作"}
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return {"ok": False, "error": "回执缺少明确执行结果"}
        if operation["status"] not in {"running", "cancel_requested"}:
            return {"ok": False, "error": "操作不在执行中，拒绝覆盖终态", "status": operation["status"]}
        done = self.operations.finish(action_id, result)
        status = "done" if done["status"] == "succeeded" else done["status"]
        result = done["result"]
        self.store.set_action_status(self.conversation_id, action_id, status, result)
        text = ("宿主已确认操作完成。" if status == "done" else
                "宿主操作未完成：" + str(result.get("error") or done["status"]))
        assistant = {"role": "assistant", "mode": "action", "content": text,
                     "action_id": action_id, "operation_id": action_id,
                     "status": status, "result": result, "artifacts": done["artifacts"]}
        self.conversation = self.store.append(self.conversation_id, assistant)
        return {"ok": bool(result.get("ok")), "ui_only": False, "action": operation["kind"],
                "action_id": action_id, "operation_id": action_id, "status": done["status"],
                "result": result, "message": assistant, "conversation_id": self.conversation_id}

    def ask(self, message: str, mode: str = "agent", run_id: str = "") -> dict:
        text = str(message or "").strip()
        if not text:
            raise ChatError("empty_message", "请输入消息")
        self._request_budget = AgentBudget(max_seconds=900)
        self.run_id = str(run_id or uuid.uuid4().hex)
        self.conversation = self.store.append(self.conversation_id, {
            "role": "user", "content": text, "run_id": self.run_id})
        self._current_seq = self.conversation["messages"][-1]["seq"]
        self.turn_id = str(self._current_seq)
        if str(mode).lower() == "chat":
            try:
                answer = self._direct_answer(text, chat_mode=True)
                request_ok = True
                meta = {"mode": "chat", "status": "completed", "answer_status": "answered",
                        "answered": True, "source_validated": False, "semantic_verified": False,
                        "usage": self._last_model_result.get("usage", {}),
                        "budget": self._request_budget.as_dict(), "errors": [], "unsupported": []}
            except ChatError as exc:
                request_ok = False
                answer = "模型暂时无法回答：" + exc.message
                meta = {"mode": "chat", "status": "failed", "answer_status": "failed",
                        "answered": False, "source_validated": False, "semantic_verified": False,
                        "error_code": exc.code, "errors": [{"code": exc.code, "message": exc.message}],
                        "unsupported": [], "budget": self._request_budget.as_dict()}
        else:
            loop = AgentLoop(project_root=self.project_root, question=text,
                data_dir=self.data_dir, model_client=self.model_client, stream=self.stream, mode="auto",
                max_steps=self.max_steps, max_tool_calls=self.max_tool_calls,
                max_seconds=900.0 if self.max_steps == 0 else 120.0,
                max_continuations=1, history=self._context(exclude_seq=self._current_seq),
                thread_id=self.conversation_id, turn_id=self.turn_id, run_id=self.run_id,
                progress=self.progress)
            report = loop.run()
            request_ok = bool(report.get("ok"))
            self._request_budget = loop.budget
            answer = _clean_answer_text(str(report.get("answer") or "本轮没有得到可核验的结果。"))
            action_rows = []
            for raw in report.get("ui_actions") or []:
                action_rows.append({"id": raw.get("action_id") or uuid.uuid4().hex,
                    "type": raw.get("type"), "label": raw.get("label") or "待确认操作",
                    "payload": raw.get("payload") or {}, "status": "pending",
                    "parent_id": raw.get("operation_id") or "",
                    "tool_call_id": raw.get("tool_call_id") or "",
                    "task_id": report.get("task_id") or "",
                    "run_id": self.run_id, "turn_id": self.turn_id,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            if action_rows:
                self.conversation = self.store.add_actions(self.conversation_id, action_rows)
            meta = {"mode": "agent", "citations": report.get("citations", []),
                    "invalid_citations": report.get("invalid_citations", []),
                    "plan": report.get("plan", []), "observations": report.get("observations", []),
                    "usage": report.get("usage", {}), "grounded": report.get("grounded", False),
                    "answered": report.get("answered", False),
                    "answer_status": report.get("answer_status", "failed"),
                    "errors": report.get("errors", []), "unsupported": report.get("unsupported", []),
                    "coverage": report.get("coverage", {}), "source_refs": report.get("source_refs", []),
                    "source_validated": report.get("source_validated", False),
                    "semantic_verified": report.get("semantic_verified", False),
                    "confidence": report.get("confidence"), "budget": report.get("budget", {}),
                    "stop_reason": report.get("stop_reason", ""),
                    "task_id": report.get("task_id", ""), "status": report.get("status", ""),
                    "trace": report.get("trace", ""), "elapsed_ms": report.get("elapsed_ms", 0),
                    "actions": [{"id": a["id"], "type": a["type"], "label": a["label"],
                                 "status": a["status"]} for a in action_rows]}
        assistant = {"role": "assistant", "content": _clean_answer_text(str(answer)),
                     "ts": round(time.time(), 3), "run_id": self.run_id, "turn_id": self.turn_id, **meta}
        self.conversation = self.store.append(self.conversation_id, assistant)
        # A failed request must not trigger a hidden same-provider summary call.
        if request_ok:
            self._roll_summary()
        return {"ok": request_ok, "conversation_id": self.conversation_id,
                "answer_status": assistant.get("answer_status"),
                "run_id": self.run_id, "turn_id": self.turn_id,
                "title": self.conversation.get("title", "新对话"),
                "message": assistant, "conversation": self.conversation}
