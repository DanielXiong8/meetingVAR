"""Desktop RTASR: 16 kHz, signed 16-bit mono PCM, one background socket.

Call start(), feed(pcm, absolute_start_seconds), poll(), then finish()/stop().
feed never waits for the network; BufferError means the *whole* block was
rejected (also reported through a status event). Unsent PCM is limited to 30 s.
Arbitrary byte fragments are joined into 1280-byte/40-ms frames. A timestamp
gap or overlap drains the old connection before starting a new media epoch.

The verified service uses st.type=1 for partial, st.type=0 for final. Pending
partials replace one another; a final replaces its pending partial and is
emitted once per connection/seg_id. Partials with missing/zero ed temporarily
use bg as their end; finals always require valid bg and ed. No speaker
identity is inferred.
connect, when supplied, has the websockets.connect(url, **kwargs) interface;
it may return an awaitable socket or an asynchronous context manager.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import math
import re
import threading
import time
import urllib.parse
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

FRAME_BYTES = 1280
FRAME_SECONDS = 0.04
BYTES_PER_SECOND = 32000
MAX_PENDING_BYTES = 30 * BYTES_PER_SECOND
MAX_PENDING_BLOCKS = 4096
TIMESTAMP_TOLERANCE = 0.5 / 16000
CONNECT_TIMEOUT = 10.0
STARTED_TIMEOUT = 8.0
SEND_TIMEOUT = 5.0
FINAL_TIMEOUT = 8.0
AUDIO_IDLE_TIMEOUT = 10.0  # service disconnects after about 15 s without PCM
CLOSE_TIMEOUT = 0.5
STOP_TIMEOUT = 2.0
RECONNECT_DELAY = 0.5


def _credential_kind(path: Path, root: Path | None = None) -> str | None:
    # The nearest service-specific name wins, including the filename itself.
    for part in (path, *path.parents):
        name = part.name.casefold()
        if "听写" in name or re.search(r"(?:^|[^a-z])iat(?:[^a-z]|$)", name):
            return "iat"
        if any(hint in name for hint in ("实时语音转写", "实时转写", "rtasr")):
            return "rtasr"
        if part == root:
            break
    return None


def find_rtasr(path: str | Path) -> Path | None:
    """Select by names only; never inspect unrelated credential contents."""
    path = Path(path)
    try:
        if path.is_file():
            if path.suffix.lower() != ".env":
                return None
            if _credential_kind(path) == "iat":
                raise ValueError("所选文件属于 IAT 语音听写；RTASR 实时转写需要独立凭证和权限。")
            return path  # an explicitly selected, neutrally named file is OK
        if not path.is_dir():
            return None
        hits = sorted(p for p in path.rglob("*.env")
                      if p.is_file() and _credential_kind(p, path) == "rtasr")
    except OSError:
        raise ValueError("无法定位 RTASR 凭证文件。") from None
    if len(hits) > 1:
        raise ValueError("找到多个 RTASR 凭证文件；请明确指定其中一个 .env 文件。")
    return hits[0] if hits else None


def load_creds(path: str | Path) -> dict[str, str]:
    selected = find_rtasr(path)
    if selected is None:
        raise ValueError("未找到 RTASR 凭证；目录或 .env 文件名需含实时语音转写、实时转写或 rtasr，不能使用 IAT 听写凭证。")
    try:
        lines = selected.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("无法读取选中的 RTASR 凭证文件。") from None
    creds = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if separator and key in {"APPID", "APIKey", "APISecret"} and value:
            creds[key] = value
    if "APISecret" in creds:
        raise ValueError("所选凭证含 APISecret，属于 IAT 听写格式；RTASR 仅使用 APPID/APIKey，请选择实时转写凭证。")
    if not {"APPID", "APIKey"} <= creds.keys():
        raise ValueError("RTASR 凭证必须包含非空 APPID 和 APIKey（区别于 IAT 语音听写）。")
    return creds


def make_signa(appid: str, api_key: str, ts: str) -> str:
    digest = hashlib.md5(f"{appid}{ts}".encode()).hexdigest().encode()
    return base64.b64encode(hmac.new(api_key.encode(), digest, hashlib.sha1).digest()).decode()


def ws_url(creds: dict[str, str]) -> str:
    ts = str(int(time.time()))
    query = urllib.parse.urlencode({
        "appid": creds["APPID"], "ts": ts,
        "signa": make_signa(creds["APPID"], creds["APIKey"], ts), "lang": "cn",
    })
    return "wss://rtasr.xfyun.cn/v1/ws?" + query


def _ws_connect(url: str, **kwargs):
    import websockets

    if "proxy" in inspect.signature(websockets.connect).parameters:
        kwargs["proxy"] = None
    return websockets.connect(url, **kwargs)


class _SessionError(Exception):
    """Only static, locally written messages may be carried by this exception."""


@dataclass
class _Block:
    pcm: bytes
    start_ts: float
    offset: int = 0


@dataclass
class _Connection:
    epoch: str
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    origin: float | None = None
    sent_bytes: int = 0
    end_sent: bool = False
    finalized: set[str] = field(default_factory=set)


class XfyunRealtime:
    with_embeddings = False
    sentence_timestamps = True

    def __init__(self, cred_path: str | Path, *, connect=None):
        self._creds = load_creds(cred_path)
        self._connect = connect if connect is not None else _ws_connect
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._pending: deque[_Block] = deque()
        self._pending_bytes = 0
        self._events: OrderedDict[object, dict] = OrderedDict()
        self._namespace = uuid.uuid4().hex
        self._started = False
        self._finishing = False
        self._stopping = False
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None

    def _status(self, state: str, message: str) -> None:
        with self._event_lock:
            self._events[object()] = {"type": "status", "state": state, "message": message}

    def _text_event(self, event: dict) -> None:
        with self._event_lock:
            key = event["id"]
            self._events.pop(key, None)
            self._events[key] = event

    def poll(self) -> list[dict]:
        """Atomically remove all pending events; partials are latest snapshots."""
        with self._event_lock:
            events = list(self._events.values())
            self._events.clear()
        return events

    def start(self) -> None:
        """Start immediately; DNS, handshake and started are all background work."""
        with self._lock:
            if self._done.is_set() or self._stopping:
                raise RuntimeError("RTASR 已停止；请创建新实例。")
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(target=self._thread_main, name="xfyun-rtasr", daemon=True)
            self._thread.start()

    def _signal(self, *, cancel: bool = False) -> None:
        with self._lock:
            loop, wake, task = self._loop, self._wake, self._task
        if loop is not None:
            try:
                if cancel and task is not None:
                    loop.call_soon_threadsafe(task.cancel)
                elif wake is not None:
                    loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # loop already closed during shutdown
                pass

    def feed(self, pcm: bytes, start_ts: float) -> None:
        """Queue one complete block or raise BufferError; never wait for space."""
        if not isinstance(pcm, bytes):
            raise TypeError("RTASR PCM 必须为 bytes。")
        if isinstance(start_ts, bool) or not isinstance(start_ts, (int, float)):
            raise ValueError("RTASR start_ts 必须为有限的绝对秒数。")
        try:
            timestamp = float(start_ts)
        except (ValueError, OverflowError):
            raise ValueError("RTASR start_ts 必须为有限的绝对秒数。") from None
        if not math.isfinite(timestamp):
            raise ValueError("RTASR start_ts 必须为有限的绝对秒数。")
        with self._lock:
            if not self._started or self._finishing or self._stopping or self._done.is_set():
                raise RuntimeError("RTASR 未启动或正在结束，不能接收 PCM。")
            if not pcm:
                return
            if self._pending_bytes + len(pcm) > MAX_PENDING_BYTES or len(self._pending) >= MAX_PENDING_BLOCKS:
                message = "RTASR PCM 队列已满，本次音频块未接收；请保留原始时间戳后重试或报告丢帧。"
                self._status("error", message)
                raise BufferError(message)
            self._pending.append(_Block(pcm, timestamp))
            self._pending_bytes += len(pcm)
        self._signal()

    def finish(self, timeout: float = 10) -> list[dict]:
        """Drain at real-time pace, send end, and await ls within the total budget.

        A timeout reports an error and requests background cancellation; stop()
        can also be used to synchronously join the short socket cleanup.
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("RTASR finish timeout 必须为非负有限秒数。")
        try:
            budget = float(timeout)
        except (ValueError, OverflowError):
            raise ValueError("RTASR finish timeout 必须为非负有限秒数。") from None
        if not math.isfinite(budget) or budget < 0:
            raise ValueError("RTASR finish timeout 必须为非负有限秒数。")
        with self._lock:
            started = self._started
            self._finishing = True
        if not started:
            self.stop()
            return self.poll()
        self._signal()
        if not self._done.wait(budget):
            self._status("error", "RTASR finish 超时；已请求关闭，未确认的音频或最终文本可能缺失。")
            self._request_stop()
        return self.poll()

    def _request_stop(self) -> None:
        with self._lock:
            already_stopping = self._stopping
            self._stopping = True
        if not already_stopping:
            self._signal(cancel=True)

    def stop(self) -> None:
        """Cancel socket work and reclaim the worker, with bounded cleanup."""
        self._request_stop()
        with self._lock:
            thread = self._thread
        if thread is None:
            if not self._done.is_set():
                self._status("stopped", "RTASR 已停止。")
                self._done.set()
        elif thread is not threading.current_thread():
            thread.join(STOP_TIMEOUT)
            if thread.is_alive():
                self._status("error", "RTASR 后台连接关闭超时。")

    def _thread_main(self) -> None:
        # A private selector loop also works on Windows without changing the
        # desktop application's global asyncio policy.
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            with self._lock:
                self._loop = loop
                self._wake = asyncio.Event()
                self._task = loop.create_task(self._run())
                task = self._task
                stopping = self._stopping
            if stopping:
                task.cancel()
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                self._status("error", "RTASR 后台连接异常，已停止。")
        finally:
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            asyncio.set_event_loop(None)
            with self._lock:
                self._loop = None
                self._task = None
                self._wake = None
                self._pending.clear()
                self._pending_bytes = 0
            self._status("stopped", "RTASR 已停止。")
            self._done.set()

    async def _run(self) -> None:
        epoch = 0
        self._status("connecting", "正在连接 RTASR 实时转写。")
        while True:
            with self._lock:
                if self._stopping:
                    return
            epoch += 1
            state = _Connection(f"rtasr:{self._namespace}:{epoch}")
            retry_delay = 0.0
            try:
                reason = await self._session(state)
                if reason == "finish":
                    return
                message = "PCM 时间戳不连续，正在重连并重新锚定媒体时间。"
            except ImportError:
                self._status("error", "RTASR 缺少 websockets 依赖，无法连接。")
                return
            except _SessionError as exc:
                self._status("error", str(exc))
                message = "RTASR 连接中断，正在重新连接；未确认的文本可能缺失。"
                retry_delay = RECONNECT_DELAY
            except Exception:
                # Never expose exception strings: transports often include the
                # entire signed URL, app id, server desc or credential paths.
                self._status("error", "RTASR 连接失败或中断；未确认的文本可能缺失。")
                message = "正在重新连接 RTASR 实时转写。"
                retry_delay = RECONNECT_DELAY
            with self._lock:
                if self._stopping or (self._finishing and not self._pending):
                    return
            self._status("reconnecting", message)
            await asyncio.sleep(retry_delay)

    async def _session(self, state: _Connection) -> str:
        ws = None
        manager = None
        tasks = []
        try:
            opening = self._connect(ws_url(self._creds), max_size=2**22,
                                    open_timeout=CONNECT_TIMEOUT, close_timeout=CLOSE_TIMEOUT,
                                    ping_interval=20, ping_timeout=20)
            try:
                if inspect.isawaitable(opening):
                    ws = await asyncio.wait_for(opening, CONNECT_TIMEOUT)
                else:
                    manager = opening
                    ws = await asyncio.wait_for(manager.__aenter__(), CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                raise _SessionError("RTASR 建立连接超时。") from None
            receiver = asyncio.create_task(self._receive(ws, state))
            started = asyncio.create_task(state.ready.wait())
            tasks.extend((receiver, started))
            done, _ = await asyncio.wait((receiver, started), timeout=STARTED_TIMEOUT,
                                         return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                raise _SessionError("RTASR 在 started 前提前结束。")
            if started not in done:
                raise _SessionError("RTASR 等待 started 超时，尚未发送 PCM。")
            self._status("ready", "RTASR 已就绪。")
            sender = asyncio.create_task(self._send_audio(ws, state))
            tasks.append(sender)
            done, _ = await asyncio.wait((receiver, sender), return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                if not state.end_sent:
                    raise _SessionError("RTASR 服务端提前结束；未确认的文本可能缺失。")
            reason = await sender
            try:
                await asyncio.wait_for(receiver, FINAL_TIMEOUT)
            except asyncio.TimeoutError:
                raise _SessionError("RTASR 等待最终结果超时；未确认的文本可能缺失。") from None
            return reason
        finally:
            # stop/finish can arrive while an earlier timeout is already
            # cancelling the receiver. Protect the whole cleanup so that this
            # cancellation cannot skip socket.close(). Its timeout stays bounded.
            cleanup = asyncio.create_task(self._close_session(ws, manager, tasks))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def _close_session(self, ws, manager, tasks) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if ws is not None:
            try:
                closing = manager.__aexit__(None, None, None) if manager is not None else ws.close()
                await asyncio.wait_for(closing, CLOSE_TIMEOUT)
            except Exception:
                self._status("error", "RTASR socket 关闭失败或超时。")

    def _peek_audio(self) -> tuple[bytes, float] | None:
        # Nothing is removed until send succeeds. On failure the unsent frame,
        # including any partial frame, therefore survives into the next epoch.
        with self._lock:
            if not self._pending:
                return None
            first = self._pending[0]
            origin = first.start_ts + first.offset / BYTES_PER_SECOND
            data = bytearray()
            boundary = False
            for block in self._pending:
                actual = block.start_ts + block.offset / BYTES_PER_SECOND
                expected = origin + len(data) / BYTES_PER_SECOND
                if abs(actual - expected) > TIMESTAMP_TOLERANCE:
                    boundary = True
                    break
                take = min(FRAME_BYTES - len(data), len(block.pcm) - block.offset)
                data.extend(block.pcm[block.offset:block.offset + take])
                if len(data) == FRAME_BYTES:
                    break
            if len(data) == FRAME_BYTES or boundary or self._finishing:
                return bytes(data), origin
            return None

    def _commit_audio(self, size: int) -> None:
        with self._lock:
            self._pending_bytes -= size
            while size:
                block = self._pending[0]
                take = min(size, len(block.pcm) - block.offset)
                block.offset += take
                size -= take
                if block.offset == len(block.pcm):
                    self._pending.popleft()

    async def _send_audio(self, ws, state: _Connection) -> str:
        next_send: float | None = None
        loop = asyncio.get_running_loop()
        while True:
            self._wake.clear()
            frame = self._peek_audio()
            with self._lock:
                finished = self._finishing and not self._pending
            if frame is None and not finished:
                try:
                    await asyncio.wait_for(self._wake.wait(), AUDIO_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    raise _SessionError("RTASR 等待 PCM 超时，正在重建连接。") from None
                continue
            # Anchor when the first PCM arrives, then keep cumulative monotonic
            # deadlines so normal scheduler overshoot is paid back next frame.
            if next_send is None:
                next_send = loop.time()
            await asyncio.sleep(max(0.0, next_send - loop.time()))
            gap = (frame is not None and state.origin is not None and
                   abs(frame[1] - (state.origin + state.sent_bytes / BYTES_PER_SECOND)) > TIMESTAMP_TOLERANCE)
            if finished or gap:
                state.end_sent = True
                await self._send(ws, json.dumps({"end": True}))
                return "finish" if finished else "gap"
            pcm, timestamp = frame
            if state.origin is None:
                state.origin = timestamp
            wire_pcm = pcm
            if len(wire_pcm) % 2:
                self._status("error", "PCM 尾部缺少半个 16-bit 采样点，已显式补一个静音字节。")
                wire_pcm += b"\x00"
            await self._send(ws, wire_pcm)
            self._commit_audio(len(pcm))
            state.sent_bytes += len(wire_pcm)
            # Keep at most one immediately due frame after a long stall. After
            # that compensation, the following deadline is in the future;
            # excess timing debt is discarded instead of causing a burst.
            next_send = max(next_send + len(wire_pcm) / BYTES_PER_SECOND, loop.time())

    async def _send(self, ws, payload) -> None:
        try:
            await asyncio.wait_for(ws.send(payload), SEND_TIMEOUT)
        except asyncio.TimeoutError:
            raise _SessionError("RTASR 发送超时；未确认的文本可能缺失。") from None
        except Exception:
            raise _SessionError("RTASR 发送中断；未确认的文本可能缺失。") from None

    async def _receive(self, ws, state: _Connection) -> None:
        while True:
            try:
                raw = await ws.recv()
            except Exception:
                raise _SessionError("RTASR 接收连接中断，未收到完整结束确认；最终文本可能缺失。") from None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError
            except (TypeError, ValueError, UnicodeError):
                self._status("error", "RTASR 返回了无效消息。")
                continue
            if message.get("action") == "error" or message.get("code") not in (None, 0, "0"):
                raise _SessionError("RTASR 服务端报告错误；请核对实时转写权限、凭证和服务状态（区别于 IAT）。")
            action = message.get("action")
            if action == "started":
                state.ready.set()
            elif action == "result":
                last = self._result(message.get("data"), state)
                if last:
                    return
            else:
                self._status("error", "RTASR 返回了未知消息类型。")

    def _result(self, data, state: _Connection) -> bool:
        last = False
        try:
            data = json.loads(data) if isinstance(data, (str, bytes)) else data
            if not isinstance(data, dict):
                raise ValueError
            last = data.get("ls") is True or data.get("ls") == "true"
            root = data.get("cn") if "cn" in data else data.get("en")
            st = root["st"]
            kind = str(st["type"])
            if kind not in {"0", "1"}:
                raise ValueError
            seg_id = data["seg_id"]
            if isinstance(seg_id, bool) or not isinstance(seg_id, (str, int)) or str(seg_id) == "":
                raise ValueError
            offsets = []
            for name in ("bg", "ed"):
                value = st.get(name)  # zero is meaningful; never use `value or ...`
                if name == "ed" and kind == "1" and value is None:
                    offsets.append(offsets[0])
                    continue
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise ValueError
                number = float(value)
                if not math.isfinite(number) or number < 0:
                    raise ValueError
                # RTASR may not yet know a partial's end. This provisional
                # endpoint is restricted to type=1 and never used for finals.
                offsets.append(offsets[0] if name == "ed" and kind == "1" and number == 0
                               else number / 1000)
            if offsets[1] < offsets[0] or state.origin is None:
                raise ValueError
            begin, end = (state.origin + offset for offset in offsets)
            if not math.isfinite(begin) or not math.isfinite(end):
                raise ValueError
            text = "".join(cw["w"] for rt in st["rt"] for word in rt["ws"] for cw in word["cw"])
            event_id = f"{state.epoch}:{seg_id}"
            if event_id not in state.finalized:
                event = {"type": "final" if kind == "0" else "partial", "id": event_id,
                         "text": text, "start_ts": begin, "end_ts": end, "speaker": "spk?"}
                if kind == "0":
                    state.finalized.add(event_id)
                self._text_event(event)
            if last and kind != "0":
                self._status("error", "RTASR 结束标记未携带 final 结果。")
        except (KeyError, TypeError, ValueError, OverflowError):
            self._status("error", "RTASR 结果的结构、句子 ID 或 bg/ed 时间戳无效；该结果未发布。")
        return last
