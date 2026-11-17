"""meetingVAR 桌面壳（oCam 风格 v0.1）：pywebview 窗口 + CLI 子进程桥。

架构（调研报告 §4）：UI 只做壳——
- 开会 = 启动引擎子进程 `python -m meetingvar go/record …`（cwd=仓库根）；
- 事件 = 引擎 stdout 按行解析成结构化事件，前端轮询 poll_events()；
- 输入 = 手动 VAR 写一个空行、点名应答写名字——正好对接引擎现有的 input() 线程；
- 结束 = CTRL_BREAK_EVENT → 引擎走键盘中断的优雅收尾（尾块转写/零落盘纪律不变）。
UI 不 import 引擎核心、不碰保存链路。

命令行（供构建/自检）：
    python app.py              正常启动
    python app.py --selftest   开窗 14 秒自动关闭并打印 SELFTEST-OK（CI/打包冒烟）
    python app.py --smoke-engine  不开窗口，直接演练"起会→手动VAR→停止"全链路
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from uuid import UUID, uuid4
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

# 注意：webview 延迟导入（仅 pick_folder/main 需要）——让本模块在无 pywebview 的
# 测试环境（CI model-free）里可 import，后端逻辑可单测。

APP_NAME = "meetingVAR"
APP_VERSION = "0.28.4"

# 冻结（PyInstaller onefile）时资源在 _MEIPASS；源码运行时在本目录
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = _BASE / "web"
WEB_NEXT = _BASE / "web-next"
ASSETS_DIR = _BASE / "assets"
BRIDGE_PY = _BASE / "engine_bridge.py"


def bridge_path() -> Path:
    """Run the bridge outside _MEIPASS when frozen.

    Windows resolves extension DLLs from the script directory first. Running
    engine_bridge.py directly from PyInstaller's _MEI directory makes conda
    Python load the bundled sqlite3.dll, which then fails on _sqlite3 import
    (and every agent command that touches memory dies). A plain copy in
    %APPDATA%\meetingVAR has no bundled DLLs next to it.
    """
    if not getattr(sys, "frozen", False):
        return BRIDGE_PY
    try:
        target = CONFIG_DIR / "engine_bridge.py"
        data = BRIDGE_PY.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return target
    except OSError:
        return BRIDGE_PY

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "ui_config.json"
LOG_FILE = CONFIG_DIR / "ui.log"

# 子进程一律无控制台窗口（GUI 壳是 windowed 程序，控制台子进程默认会弹黑窗——
# 实测用户点开始会议/读取/起 LLM 都会弹"脚本页"，且会议期间一直挂着）。
# 输出照旧走管道/日志文件，不受影响。
_SPAWN_FLAGS = 0
if os.name == "nt":
    _SPAWN_FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

_APP_HOME = (Path(sys.executable).resolve().parent
             if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1])
_RUNTIME = _APP_HOME / "runtime"
ENGINE_PY_CANDIDATES = [
    os.environ.get("MEETINGVAR_ENGINE_PYTHON", ""),
    str(_RUNTIME / "python" / "python.exe"),
    str(_RUNTIME / "python.exe"),
]
REPO_CANDIDATES = [
    os.environ.get("MEETINGVAR_REPO", ""),
    str(_RUNTIME / "repo"),
    str(_RUNTIME),
    str(_APP_HOME),
]

CONFIG_DEFAULTS = {
    "engine_python": "",
    "repo_dir": "",
    "device": "auto",
    "last_project": "",
    "projects": [],          # 已知项目（会议归档分组用；start_meeting 时维护）
    "llm_gguf": "",
    "llm_backend": "auto",
    "llm_server_exe": "",
    "llm_context_tokens": 16384,
    "llm_gpu_layers": -1,
    "llm_device": "",
    "llm_reading_max_tokens": 8192,
    "llm_api_context_tokens": 500000,
    "llm_thinking": "",
    "llm_reasoning_effort": "",
    "use_llm": False,        # 旧版配置兼容，实时兴趣由 interest_mode 控制
    "interest_mode": "manual",
    "interest_focus": "",
    "meeting_engine_version": "0.1.2",
    "audio_source": "both",
    "asr_engine": "local",
    "xfyun_dir": "",         # 讯飞控制台下载的文件夹（自动认领 .env：听写 / 录音文件转写）
    "llm_provider": "local", # 模型服务：local=内置 llama.cpp 服务 / custom=自定义 OpenAI 兼容 API
    "llm_base_url": "",      # custom: 形如 https://api.xxx.com/v1
    "llm_model": "",         # custom: 模型名
    "llm_api_key": "",       # custom: API Key（仅存本机配置）
    "license_token": "",
    "trial_started": "",
    "telemetry_enabled": False,
    "telemetry_endpoint": "",
    "crash_reports_enabled": True,
    "update_manifest_url": "",
    "auto_update_check": False,
    "data_retention_days": 0,
}

SESSMAP_FILE = CONFIG_DIR / "sessions_map.json"
LLM_LOG_FILE = CONFIG_DIR / "llm_server.log"
LLM_PORT = 11439

_LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _webview2_available() -> bool:
    """WebView2 is a Windows component, but older clean images may lack it."""
    if os.name != "nt":
        return True
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "EdgeWebView" / "Application",
    ]
    if any(root.is_dir() and any(root.iterdir()) for root in roots):
        return True
    try:
        import winreg

        key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            winreg.QueryValueEx(handle, "pv")
            return True
    except (ImportError, OSError):
        return False


def _first_existing(paths: list[str]) -> str:
    for p in paths:
        if p and Path(p).exists():
            return p
    return ""


class _NextWebHandler(SimpleHTTPRequestHandler):
    """Local static server for the exported Next UI."""

    def log_message(self, *args):  # noqa: A003
        pass


class _ProjectFileHandler(SimpleHTTPRequestHandler):
    """Read-only project file server (PDF native viewer, large media).

    Each token is permanently bound to one resolved project root. Old URLs
    cannot be retargeted by switching projects; GET and HEAD share validation.
    """

    def log_message(self, *args):  # noqa: A003
        pass

    def _target(self, path):
        parts = unquote(urlsplit(path).path).lstrip("/").split("/", 1)
        if len(parts) != 2 or not parts[1] or "\x00" in parts[1]:
            return None
        root = self.server.project_roots.get(parts[0])
        if root is None:
            return None
        rel = Path(parts[1].replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            return None
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target

    def translate_path(self, path):
        target = self._target(path)
        return str(target) if target is not None else ""

    def send_head(self):
        self._remaining = None
        requested = self.headers.get("Range")
        path = self._target(self.path)
        if path is None:
            self.send_error(404)
            return None
        if not requested or not path.is_file():
            return super().send_head()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
        size = path.stat().st_size
        if not match or not any(match.groups()) or not size:
            self.send_error(416, "Invalid byte range")
            return None
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(size - 1, int(last)) if first and last else size - 1
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        try:
            stream = path.open("rb")
        except OSError:
            self.send_error(404)
            return None
        stream.seek(start)
        self._remaining = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._remaining))
        self.end_headers()
        return stream

    def copyfile(self, source, outputfile):
        if self._remaining is None:
            return super().copyfile(source, outputfile)
        while self._remaining > 0:
            block = source.read(min(65536, self._remaining))
            if not block:
                break
            outputfile.write(block)
            self._remaining -= len(block)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(saved)
            try:
                saved_context = int(cfg.get("llm_api_context_tokens") or 0)
            except (TypeError, ValueError):
                saved_context = 0
            if (str(cfg.get("llm_provider") or "").lower() == "custom"
                    and "agnes" in str(cfg.get("llm_model") or "").casefold()
                    and saved_context < 500000):
                cfg["llm_api_context_tokens"] = 500000
            if saved.get("meeting_engine_version") != "0.1.2":
                cfg.update(asr_engine="xfyun_rtasr", interest_mode="api", llm_provider="custom",
                           meeting_engine_version="0.1.2")
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Backend:
    def __init__(self) -> None:
        self._cfg = load_config()
        if not self._cfg.get("trial_started"):
            self._cfg["trial_started"] = time.strftime("%Y-%m-%d")
            save_config(self._cfg)
        self._window: webview.Window | None = None
        self._tray_icon = None
        self._tray_available = False
        self._tray_quit = False
        self._tray_notified = False
        self._proc: subprocess.Popen | None = None
        self._control_path: Path | None = None
        self._reader_thread: threading.Thread | None = None
        self._events: list[dict] = []
        self._events_lock = threading.Lock()
        self._event_seq = 0
        self._meeting_started_at: float | None = None
        self._screen_state: dict = {}
        self._meeting_summary: dict = {}
        self._capture_phase = "idle"
        self._server: ThreadingHTTPServer | None = None
        self._server_port = 0
        self._project_server: ThreadingHTTPServer | None = None
        self._project_server_port = 0
        self._project_token = ""
        self._project_roots: dict[str, Path] = {}
        self._project_tokens: dict[Path, str] = {}
        self._project_server_lock = threading.Lock()
        self._page_ready = False
        self._current_project = ""
        self._last_log = ""
        self._chat_proc: subprocess.Popen | None = None
        self._chat_lock = threading.Lock()
        self._chat_cancelled = False
        self._chat_identity: dict | None = None
        self._chat_seen_runs: set[str] = set()
        self._action_lock = threading.Lock()
        self._meeting_lock = threading.RLock()
        self._und_lock = threading.RLock()
        self._llm_proc: subprocess.Popen | None = None
        self._llm_launch: dict = {}
        self._llm_error = ""
        self._llm_log_offset = 0
        self._llm_start_lock = threading.Lock()
        self._llm_verified: set[str] = set()
        self._llm_verified_at: dict[str, float] = {}
        try:
            validations = json.loads((CONFIG_DIR / "llm_validation.json").read_text(encoding="utf-8"))
            # Verification is bound to the exact provider fingerprint. It stays valid
            # until that configuration changes or a real call fails auth/config;
            # a fixed 24h expiry only forced needless re-testing every day.
            self._llm_verified_at = {key: stamp for key, stamp in validations.items()
                                     if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                                     and isinstance(stamp, (int, float)) and stamp >= 0}
            self._llm_verified.update(self._llm_verified_at)
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        # 文件转写任务（后台线程 + 进度轮询）
        self._tf: dict = {"running": False, "done": False, "ok": False,
                          "file": "", "progress": "", "out": "", "error": ""}
        # 全库理解任务（后台进程 + [progress] 行 + 断点续跑缓存；可取消）
        self._und: dict = {"running": False, "done": False, "ok": False, "folder": "",
                           "progress": "", "result": {}, "error": "", "max_chunks": 0}
        self._und_proc: subprocess.Popen | None = None
        # 会话→项目 映射（会议库按项目分组；go 结束行 "[done] session=go_x" 时登记）
        self._session_projects: dict[str, str] = {}
        try:
            if SESSMAP_FILE.exists():
                data = json.loads(SESSMAP_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._session_projects = {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            pass
        self._autodetect()

    def _save_session_map(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SESSMAP_FILE.write_text(
                json.dumps(self._session_projects, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ---------------- 基础 ----------------
    def _autodetect(self) -> None:
        if not self._cfg.get("engine_python") or not Path(self._cfg["engine_python"]).exists():
            self._cfg["engine_python"] = _first_existing(ENGINE_PY_CANDIDATES)
        if not self._cfg.get("repo_dir") or not Path(self._cfg["repo_dir"]).exists():
            self._cfg["repo_dir"] = _first_existing(REPO_CANDIDATES)
        if not self._cfg.get("llm_gguf") or not Path(self._cfg["llm_gguf"]).exists():
            hits = []
            for base in (_RUNTIME / "models", Path("D:/env/models/models")):
                try:
                    hits.extend(sorted(base.glob("**/*.gguf")))
                except OSError:
                    continue
            self._cfg["llm_gguf"] = str(hits[0]) if hits else ""

    def _push(self, etype: str, **fields) -> None:
        with self._events_lock:
            self._event_seq += 1
            ev = {"i": self._event_seq, "t": time.time(), "type": etype}
            ev.update(fields)
            self._events.append(ev)
            if len(self._events) > 4000:
                del self._events[:1000]

    # ---------------- 本地只读文件服务（data/ 供音频/视频播放） ----------------
    def _ensure_server(self) -> None:
        if self._server is not None:
            return
        data_dir = Path(self._cfg["repo_dir"] or ".") / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        class _Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *args):  # noqa: A003
                pass

            def send_head(self):
                self._remaining = None
                requested = self.headers.get("Range")
                path = Path(self.translate_path(self.path))
                if not requested or not path.is_file():
                    return super().send_head()
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
                size = path.stat().st_size
                if not match or not any(match.groups()) or not size:
                    self.send_error(416, "Invalid byte range")
                    return None
                first, last = match.groups()
                start = int(first) if first else max(0, size - int(last))
                end = min(size - 1, int(last)) if first and last else size - 1
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                try:
                    stream = path.open("rb")
                except OSError:
                    self.send_error(404)
                    return None
                stream.seek(start)
                self._remaining = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(str(path)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(self._remaining))
                self.end_headers()
                return stream

            def copyfile(self, source, outputfile):
                if self._remaining is None:
                    return super().copyfile(source, outputfile)
                while self._remaining > 0:
                    block = source.read(min(65536, self._remaining))
                    if not block:
                        break
                    outputfile.write(block)
                    self._remaining -= len(block)

        handler = partial(_Quiet, directory=str(data_dir))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server_port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        log(f"[ui] 媒体服务 http://127.0.0.1:{self._server_port} → {data_dir}")

    def _media_url(self, abs_path: str | None) -> str | None:
        if not abs_path:
            return None
        try:
            data_dir = (Path(self._cfg["repo_dir"]) / "data").resolve()
            p = Path(abs_path)
            if not p.is_absolute():
                p = (Path(self._cfg["repo_dir"]) / p).resolve()
            rel = p.resolve().relative_to(data_dir)
            return f"http://127.0.0.1:{self._server_port}/" + rel.as_posix()
        except (ValueError, OSError):
            return None

    def _ensure_project_server(self) -> None:
        if self._project_server is not None:
            return
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProjectFileHandler)
        server.project_roots = self._project_roots
        self._project_server = server
        self._project_server_port = server.server_address[1]
        # Roots are immutable per token, not a mutable server-wide root.
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"[ui] 项目文件服务 http://127.0.0.1:{self._project_server_port}")

    def project_file_url(self, folder: str, relative: str) -> str:
        """Serve one project file over localhost (PDF viewer needs a URL)."""
        if not folder or not Path(folder).is_dir():
            return ""
        rel = Path(str(relative).replace("\\", "/"))
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return ""
        root = Path(folder).resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return ""
        if not target.is_file():
            return ""
        with self._project_server_lock:
            self._ensure_project_server()
            token = self._project_tokens.get(root)
            if token is None:
                token = secrets.token_urlsafe(24)
                self._project_tokens[root] = token
                self._project_roots[token] = root
        return (f"http://127.0.0.1:{self._project_server_port}/"
                f"{token}/{quote(rel.as_posix())}")

    # ---------------- 引擎子进程 ----------------
    @staticmethod
    def _operation_owner_env() -> dict:
        # The long-lived desktop owns host/UI actions, not the short-lived bridge.
        if os.name != "nt":
            return {}
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created),
                                      ctypes.byref(exited), ctypes.byref(kernel_time),
                                      ctypes.byref(user_time)):
            raise OSError("无法核验桌面操作进程身份")
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return {"MEETINGVAR_OPERATION_OWNER_PID": str(os.getpid()),
                "MEETINGVAR_OPERATION_OWNER_START": f"windows:{ticks}"}

    def _engine_env(self) -> dict:
        env = os.environ.copy()
        for key in ("MEETINGVAR_OPERATION_OWNER_PID", "MEETINGVAR_OPERATION_OWNER_START"):
            env.pop(key, None)
        env.update(self._operation_owner_env())
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        models = _RUNTIME / "models"
        if models.is_dir():
            env["MEETINGVAR_MODELS"] = str(models)
        for candidate in (_RUNTIME / "bin" / "ffmpeg.exe", _RUNTIME / "ffmpeg.exe"):
            if candidate.is_file():
                env["MEETINGVAR_FFMPEG"] = str(candidate)
                break
        return env

    def _llm_env(self, provider: str | None = None, purpose: str = "reading") -> dict:
        """Use the same effective provider settings for validation and real requests."""
        from urllib.parse import urlsplit
        provider = provider or self._cfg.get("llm_provider") or "local"
        if provider == "custom":
            base = (self._cfg.get("llm_base_url") or "").strip().rstrip("/")
            model = (self._cfg.get("llm_model") or "").strip()
            if not base:
                raise ValueError("自定义 API 未配置 Base URL，请在设置页填写")
            parsed = urlsplit(base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query:
                raise ValueError("Base URL须为HTTP(S)服务地址，不能包含凭据或查询参数")
            if not model:
                raise ValueError("自定义 API 未配置模型名，请填写厂商实际提供的模型ID")
            key = (self._cfg.get("llm_api_key") or "").strip()
            if key.startswith(("http://", "https://")):
                raise ValueError("API Key 填成了网址；Base URL 与 API Key 可能填反了")
            context = self._cfg.get("llm_api_context_tokens", 500000)
            if "agnes" in model.casefold():
                context = 500000
        elif provider == "local":
            st = self.llm_status()
            if not st.get("up"):
                raise ValueError(st.get("error") or "本地模型服务未启动，请点启动服务，或切到自定义API")
            base, model, key = f"http://127.0.0.1:{LLM_PORT}/v1", st.get("model", ""), "local"
            context = st.get("context_tokens") or self._cfg.get("llm_context_tokens", 16384)
        else:
            raise ValueError("未知模型厂商")
        try:
            context = int(context)
            output = 1600 if purpose == "interest" else int(self._cfg.get("llm_reading_max_tokens", 8192))
        except (TypeError, ValueError):
            raise ValueError("模型上下文或输出预算须为整数") from None
        if context < 4096 or context > 1048576 or output < 128:
            raise ValueError("模型上下文或输出预算超出支持范围")
        output = min(output, context // 4)
        return {"MEETINGVAR_LLM_BASE_URL": base, "MEETINGVAR_LLM_API_KEY": key,
                "MEETINGVAR_LLM_MODEL": model, "MEETINGVAR_LLM_MAX_TOKENS": str(output),
                "MEETINGVAR_LLM_CONTEXT_TOKENS": str(context), "MEETINGVAR_LLM_CHUNK_CHARS": "6000",
                "MEETINGVAR_LLM_THINKING": (self._cfg.get("llm_thinking") or "") if provider == "custom" else "",
                "MEETINGVAR_LLM_REASONING_EFFORT": (self._cfg.get("llm_reasoning_effort") or "") if provider == "custom" else ""}

    @staticmethod
    def _llm_fingerprint(env: dict) -> str:
        import hashlib
        values = {key: value for key, value in env.items() if key.startswith("MEETINGVAR_LLM_")}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _provider_info(self) -> dict:
        provider = self._cfg.get("llm_provider") or "local"
        label = "自定义 API" if provider == "custom" else "本地模型"
        configured, ready, error = False, False, ""
        try:
            env = self._llm_env()
            configured = True
            ready = self._llm_fingerprint(env) in self._llm_verified
            if not ready:
                error = "已配置但尚未验证生成；请在设置页测试API，或启动并验证本地服务"
        except ValueError as exc:
            error = str(exc)
        egress = "项目文本将发送到自定义 API" if provider == "custom" else "项目文本仅发送到本机模型服务"
        return {"provider": provider, "label": label, "egress": egress,
                "configured": configured, "ready": ready, "verified": ready, "error": error}

    _VAR_RE = re.compile(r">>> VAR!")
    _LINE_RE = re.compile(r"^\s*\[(\d{1,2}:\d{2}:\d{2})\]\s*(\S+):\s*(.+)$")
    _ASK_RE = re.compile(r"\[spk\] 检测到新说话人\s*(spk\w+?)\s*[（(]")
    _SAMPLE_RE = re.compile(r"刚才说：「([^」]*)」")
    _DONE_RE = re.compile(r"\[done\] session=(\S+)")

    def _handle_line(self, raw: str) -> None:
        line = raw.rstrip("\r\n")
        prefix = "@@MEETINGVAR_EVENT "
        if line.startswith(prefix):
            try:
                event = json.loads(line[len(prefix):])
                kind = event.pop("type")
                if kind == "screen_state":
                    self._screen_state = event
                elif kind in {"line", "partial_line", "capture_state", "asr_state", "asr_stream_state", "interest_state", "audio_state", "meeting_summary", "recording_warning"}:
                    if kind == "capture_state":
                        self._capture_phase = event.get("phase", "loading")
                    elif kind == "meeting_summary":
                        self._meeting_summary = event
                    self._push(kind, **event)
            except (ValueError, KeyError, TypeError, AttributeError):
                self._push("log", text="[ui] 无法解析引擎事件")
            return
        if not line.strip():
            return
        # funasr/模型库噪声不进入事件流（空 VAD 每 5s 打一行 decoding…，会淹掉真正的事件）
        if re.search(r"\[INFO\]|scope_map|trust_remote_code|decoding, utt:", line):
            return
        self._last_log = line  # 退出时随 exit 事件带回（异常时界面上能看到最后一句话）
        if re.search(r"\[rec\]|\[done\]|Traceback|Error|失败|错误|零命中|本场", line):
            log("[engine] " + line[:500])
        self._push("log", text=line)
        m = self._DONE_RE.search(line)
        if m and self._current_project:
            self._session_projects[m.group(1)] = self._current_project
            self._save_session_map()
        if self._VAR_RE.search(line):
            self._push("hit", text=line.strip())
            return
        m = self._ASK_RE.search(line)
        if m:
            sm = self._SAMPLE_RE.search(line)
            self._push("ask", spk=m.group(1), sample=sm.group(1) if sm else "")
            return
        m = self._LINE_RE.match(line)
        if m:
            log(f"[engine-line] {m.group(2)}: {m.group(3)[:300]}")
            self._push("line", clock=m.group(1), spk=m.group(2), text=m.group(3))
            return
        if "[done]" in line or "本场点名" in line or "零命中" in line:
            self._push("status", text=line.strip())

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._handle_line(line)
        except Exception as exc:  # noqa: BLE001
            self._push("log", text=f"[ui] 读流异常: {type(exc).__name__}")
        finally:
            code = proc.wait()
            # 旧引擎的退出不能污染新会议的状态（连续开会时 reader 可能晚到）。
            if self._proc is not proc:
                return
            self._push("exit", code=code, last_log=self._last_log[-200:], summary=self._meeting_summary)
            self._capture_phase = "idle"
            self._meeting_started_at = None
            path, self._control_path = self._control_path, None
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    # ---------------- JS API ----------------
    def bootstrap(self) -> dict:
        self._ensure_server()
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "config": self._cfg,
            "meeting_running": self.is_running(),
            "media_base": f"http://127.0.0.1:{self._server_port}",
            "llm": self.llm_status(),
        }

    def client_metrics(self, info: dict | None = None) -> dict:
        """Diagnostic: record the WebView CSS viewport and DPI scale."""
        log("[ui] client_metrics " + json.dumps(info or {}, ensure_ascii=False))
        return {"ok": True}

    def page_ready(self, info: dict | None = None) -> dict:
        """前端引导完成回执（JS→Python 桥的活体证明；自检据此判定成败）。"""
        info = info or {}
        self._page_ready = True
        log(f"[ui] 前端就绪（JS 桥验证） page={info.get('page')} ver={info.get('version')}")
        return {"ok": True}

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def pick_folder(self):
        import webview

        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:  # noqa: BLE001
            log(f"[ui] 文件夹对话框失败: {exc}")
            return None
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        self._cfg["last_project"] = str(path)
        save_config(self._cfg)
        return str(path)

    def pick_xfyun_dir(self):
        """选讯飞文件夹（独立选择器：不污染 last_project）。"""
        import webview

        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:  # noqa: BLE001
            log(f"[ui] 文件夹对话框失败: {exc}")
            return None
        if not result:
            return None
        path = str(result[0] if isinstance(result, (list, tuple)) else result)
        self._cfg["xfyun_dir"] = path
        save_config(self._cfg)
        return path

    def scan_project(self, folder: str) -> dict:
        """Read the tree even without an engine; enrich with terms when available."""
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        local: dict | None = None
        local = self._agent_bridge(["scan_project", folder], timeout=120)
        # Large folders (e.g. the user's Documents root) can make token
        # extraction take minutes. The file tree is what the switch needs, so
        # return the fast local scan first and skip the expensive preview.
        if local and local.get("ok") and int(local.get("doc_count") or 0) > 200:
            return local
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if py and Path(py).exists():
            try:
                r = subprocess.run(
                    [py, str(bridge_path()), "preview", folder],
                    cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=8, env=self._engine_env(), creationflags=_SPAWN_FLAGS,
                )
                out = (r.stdout or "").strip().splitlines()
                if out:
                    data = json.loads(out[-1])
                    data["ok"] = True
                    return data
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        if local is not None:
            return local
        return {"ok": False, "error": "本地预览失败"}

    def _host_operation_begin(self, folder: str, kind: str, arguments: dict) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "请选择项目以记录本次操作"}
        identifier=str(uuid4())
        payload=self._agent_payload_file(arguments)
        try:
            result=self._agent_bridge(["host_operation_begin",folder,kind,identifier,str(payload)],timeout=30)
            return {**result,"operation_id":identifier,"folder":folder}
        finally:
            try: payload.unlink()
            except OSError: pass

    def _host_operation_finish(self, operation: dict, receipt: dict) -> dict:
        payload=self._agent_payload_file(receipt)
        try:
            result=self._agent_bridge(["host_operation_finish",operation["folder"],operation["operation_id"],str(payload)],timeout=45)
            if result.get("operation_status"):
                return {**receipt,**result}
            return {**receipt,"ok":False,"journal_pending":True,"operation_id":operation["operation_id"],
                    "error":"操作结果已返回但回执未保存，请核对原操作，勿直接重试"}
        finally:
            try: payload.unlink()
            except OSError: pass

    def _meeting_start_error(self, folder: str, mode: str) -> str:
        if self.is_running():
            return "会议已在运行"
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).is_file():
            return "未配置引擎 Python（设置页指定）"
        if mode not in {"go", "record"}:
            return "会议模式无效"
        if mode == "go" and (not folder or not Path(folder).is_dir()):
            return "项目文件夹无效"
        if self._und.get("running"):
            return "项目理解仍在运行，请等待完成或取消后再开会"
        interest = self._cfg.get("interest_mode", "manual")
        if interest not in {"api", "manual"}:
            return "请选择API兴趣判断或仅手动保存"
        if interest == "api":
            if not (self._cfg.get("llm_base_url") or "").strip() or not (self._cfg.get("llm_model") or "").strip():
                return "API兴趣判断缺少自定义API地址或模型名。请在设置中填写，或明确选择仅手动保存；不会退回热词触发。"
            try:
                self._llm_env(provider="custom", purpose="interest")
            except ValueError as exc:
                return str(exc)
        engine = self._cfg.get("asr_engine", "local")
        if engine not in {"local", "xfyun", "xfyun_rtasr"}:
            return "识别引擎设置无效"
        if engine in {"xfyun", "xfyun_rtasr"}:
            xdir = (self._cfg.get("xfyun_dir") or "").strip()
            if not xdir or not Path(xdir).exists():
                return "已选讯飞识别，但未配置讯飞文件夹（设置页 → 识别引擎）"
        return ""

    def start_meeting(self, folder: str = "", mode: str = "go") -> dict:
        with self._meeting_lock:
            error = self._meeting_start_error(folder, mode)
            if error:
                return {"ok": False, "error": error}
            project = folder or self._current_project or self._cfg.get("last_project", "")
            operation=self._host_operation_begin(project,"host.start_meeting",{"mode":mode})
            if not operation.get("ok"):
                return operation
            try:
                receipt=self._start_meeting(project,mode)
                receipt["confirmation"]="start_request_only_not_complete_recording"
                if receipt.get("ok"):
                    receipt["pid"]=self._proc.pid if self._proc else None
            except Exception:
                receipt={"ok":False,"error":"会议启动异常","outcome_unknown":True}
            return self._host_operation_finish(operation,receipt)

    def _start_meeting(self, folder: str = "", mode: str = "go") -> dict:
        if self.is_running():
            return {"ok": False, "error": "会议已在运行"}
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python（设置页指定）"}
        if mode == "go" and (not folder or not Path(folder).is_dir()):
            return {"ok": False, "error": "项目文件夹无效"}
        cmd = [py, "-m", "meetingvar", "go" if mode == "go" else "record"]
        if mode == "go":
            cmd.append(folder)
            self._cfg["last_project"] = folder
            self._current_project = folder
            projects = list(self._cfg.get("projects") or [])
            if folder not in projects:
                projects.insert(0, folder)
            self._cfg["projects"] = projects[:30]
            save_config(self._cfg)
        if self._und.get("running"):
            return {"ok": False, "error": "项目理解仍在运行，请等待完成或取消后再开会"}
        interest = self._cfg.get("interest_mode", "manual")
        if interest not in {"api", "manual"}:
            return {"ok": False, "error": "请选择API兴趣判断或仅手动保存"}
        base = (self._cfg.get("llm_base_url") or "").strip()
        model = (self._cfg.get("llm_model") or "").strip()
        if interest == "api" and (not base or not model):
            return {"ok": False, "error": "API兴趣判断缺少自定义API地址或模型名。请在设置中填写，或明确选择仅手动保存；不会退回热词触发。"}
        if interest == "api":
            from urllib.parse import urlparse
            parsed = urlparse(base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                return {"ok": False, "error": "API地址须为http/https地址，不要将密钥写入URL"}
        cmd += ["--device", self._cfg.get("device", "auto"), "--no-ask-speakers", "--interest", interest]
        cmd += ["--source", self._cfg.get("audio_source", "both")]
        engine = self._cfg.get("asr_engine", "local")
        if engine not in {"local", "xfyun", "xfyun_rtasr"}:
            return {"ok": False, "error": "识别引擎设置无效"}
        cmd += ["--asr", engine]
        if engine in {"xfyun", "xfyun_rtasr"}:
            xdir = (self._cfg.get("xfyun_dir") or "").strip()
            if not xdir or not Path(xdir).exists():
                return {"ok": False, "error": "已选讯飞识别，但未配置讯飞文件夹（设置页 → 识别引擎）"}
            cmd += ["--xfyun-cred", xdir]
        env = self._engine_env()
        env.update({"MEETINGVAR_DESKTOP_EVENTS": "1", "MEETINGVAR_CHUNK": "8",
                    "MEETINGVAR_OVERLAP": "3", "MEETINGVAR_MIN_TEXT_CHARS": "1" if engine == "xfyun_rtasr" else "2",
                    "MEETINGVAR_INTEREST_FOCUS": (self._cfg.get("interest_focus") or "")[:6000]})
        # 引擎不再在桌面模式读 stdin（Windows 管道上阻塞的读线程会饿死模型加载）。
        # 停止/手动 VAR 走控制文件，引擎在主循环里增量读取。
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self._control_path = CONFIG_DIR / f"control_{time.time_ns()}.txt"
            self._control_path.write_text("", encoding="utf-8")
            env["MEETINGVAR_CONTROL_FILE"] = str(self._control_path)
        except OSError as exc:
            self._control_path = None
            return {"ok": False, "error": f"无法创建会议控制文件: {exc}"}
        if interest == "api":
            try:
                env.update(self._llm_env(provider="custom", purpose="interest"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        self._current_project = folder
        self._screen_state = {}
        self._meeting_summary = {}
        self._capture_phase = "loading"
        self._last_log = ""
        flags = _SPAWN_FLAGS
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=env, creationflags=flags,
            )
        except OSError as exc:
            self._proc = None
            return {"ok": False, "error": f"启动失败: {exc}"}
        self._meeting_started_at = time.time()
        self._push("started", cmd=" ".join(cmd), pid=self._proc.pid)
        self._reader_thread = threading.Thread(target=self._reader, args=(self._proc,), daemon=True)
        self._reader_thread.start()
        log(f"[ui] 引擎已启动 pid={self._proc.pid}: {' '.join(cmd)}")
        return {"ok": True, "pid": self._proc.pid, "started_at": self._meeting_started_at}

    def _send_control(self, command: str) -> bool:
        path = self._control_path
        if path is None:
            return False
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(command.rstrip("\r\n") + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError as exc:
            log(f"[ui] 控制文件写入失败: {exc}")
            return False

    def _reap_engine(self, proc: subprocess.Popen, grace: float = 25.0) -> None:
        """停止后兜底：模型加载/收尾卡住时也要在有限时间内清掉引擎进程。"""
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
        log(f"[ui] 引擎 {grace:.0f}s 未退出，强制终止 pid={proc.pid}")
        try:
            proc.terminate()
            proc.wait(timeout=5)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.kill()
            log(f"[ui] 已强杀引擎 pid={proc.pid}")
        except OSError:
            pass

    def stop_meeting(self) -> dict:
        with self._meeting_lock:
            proc=self._proc
            if proc is None or proc.poll() is not None:
                return {"ok":False,"error":"没有正在运行的会议"}
            operation=self._host_operation_begin(self._current_project,"host.stop_meeting",{"pid":proc.pid})
            if not operation.get("ok"):
                return operation
            receipt=self._stop_meeting()
            if not receipt.get("ok"):
                return self._host_operation_finish(operation,receipt)
            def completed_stop():
                try:
                    code=proc.wait()
                    result={"ok":code == 0,"pid":proc.pid,"exit_code":code,
                            "confirmation":"process_exit_not_archive_verification"}
                except Exception:
                    result={"ok":False,"error":"停止结果未核验","outcome_unknown":True}
                self._host_operation_finish(operation,result)
            threading.Thread(target=completed_stop,daemon=True).start()
            return {**receipt,"operation_id":operation["operation_id"],"operation_status":"running"}

    def _stop_meeting(self) -> dict:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return {"ok": False, "error": "没有正在运行的会议"}
        # 桌面模式：控制文件是主通道；stdin 只作兼容兜底（引擎已不读 stdin）。
        sent = self._send_control(":stop")
        try:
            if proc.stdin:
                proc.stdin.write(":stop\n")
                proc.stdin.flush()
        except OSError:
            pass
        log(f"[ui] 已发送优雅停止指令（{'控制文件' if sent else 'stdin'}） pid={proc.pid}")
        threading.Thread(target=self._reap_engine, args=(proc,), daemon=True).start()
        return {"ok": True, "status": "running", "pid": proc.pid,
                "mode": "control" if sent else "stdin"}

    def _host_operation_run(self, folder: str, kind: str, arguments: dict, execute) -> dict:
        operation = self._host_operation_begin(folder, kind, arguments)
        if operation.get("ok") is not True:
            return operation
        try:
            receipt = execute()
        except Exception:
            receipt = {"ok": False, "error": "操作执行异常，请核对实际结果",
                       "outcome_unknown": True}
        return self._host_operation_finish(operation, receipt)

    def manual_var(self) -> dict:
        with self._meeting_lock:
            if not self.is_running():
                return {"ok": False, "error": "会议未在运行"}
            def submit() -> dict:
                receipt = ({"ok": True} if self._send_control(":var")
                           else self._write_stdin(":var\n"))
                return {**receipt, "confirmation": "command_sent_not_clip_verified"}
            return self._host_operation_run(self._current_project, "host.manual_var", {}, submit)

    def answer_prompt(self, text: str) -> dict:
        name = (text or "").strip() if isinstance(text, str) else ""
        if not isinstance(text, str) or any(char in name for char in ("\r", "\n", "\x00")) or name.startswith(":"):
            return {"ok": False, "error": "说话人名称必须为单行文本，不能是控制指令"}
        with self._meeting_lock:
            if not self.is_running():
                return {"ok": False, "error": "会议未在运行"}
            def submit() -> dict:
                return {**self._write_stdin(name + "\n"),
                        "confirmation": "name_sent_not_identity_verified"}
            return self._host_operation_run(self._current_project, "host.answer_prompt",
                                            {"name": name, "skipped": not name}, submit)

    def _write_stdin(self, line: str) -> dict:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return {"ok": False, "error": "会议未在运行"}
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def poll_events(self, since: int = 0, with_preview: bool = True) -> dict:
        with self._events_lock:
            evs = [e for e in self._events if e["i"] > since]
            nxt = self._event_seq
        screen = dict(self._screen_state)
        if not with_preview:
            # 预览是 base64 JPEG（~100KB/秒），新 UI 不显示它；轮询里去掉可显著减轻桥接负载。
            screen.pop("preview", None)
        return {"events": evs, "next": nxt, "running": self.is_running(),
                "started_at": self._meeting_started_at, "screen": screen,
                "phase": self._capture_phase}

    def screen_state(self) -> dict:
        """Live screen preview for the 录屏 page (separate from event polling)."""
        return {"ok": True, "screen": dict(self._screen_state),
                "phase": self._capture_phase, "running": self.is_running()}

    # ---- 会议库 ----
    def list_sessions(self) -> list[dict]:
        data = Path(self._cfg["repo_dir"] or ".") / "data"
        out = []
        pool = data / "pool"
        if pool.exists():
            for d in pool.iterdir():
                if not d.is_dir():
                    continue
                sid = d.name
                lines = clips = None
                sp = d / "summary.json"
                tj = d / "transcript.json"
                try:
                    if sp.exists():
                        s = json.loads(sp.read_text(encoding="utf-8"))
                        lines, clips = s.get("lines"), s.get("clips")
                    elif tj.exists():
                        t = json.loads(tj.read_text(encoding="utf-8"))
                        lines, clips = len(t.get("transcript", [])), len(t.get("clips", []))
                except (OSError, ValueError):
                    pass
                minutes = (data / "reports" / sid / "minutes.md").exists()
                try:
                    mtime = d.stat().st_mtime
                except OSError:
                    mtime = 0
                out.append({"sid": sid, "lines": lines, "clips": clips, "minutes": minutes,
                            "mtime": mtime, "has_transcript": tj.exists()})
        out.sort(key=lambda x: -x["mtime"])
        return out[:100]

    def usage_stats(self) -> dict:
        """Aggregate local Agent LLM traces for the usage dashboard.

        Reads only application-owned JSONL audit events; prompts and content are
        never included in the trace, so the dashboard cannot leak meeting text.
        """
        from datetime import date, timedelta

        roots = []
        for value in [self._cfg.get("last_project")] + list(self._cfg.get("projects") or []):
            if not isinstance(value, str) or not value.strip():
                continue
            path = Path(value)
            if path.is_dir() and path.resolve() not in {p.resolve() for p in roots}:
                roots.append(path)
        totals = {"tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "reasoning_tokens": 0, "calls": 0, "errors": 0}
        days = {}
        models = {}
        seen = set()
        for root in roots:
            for name in ("agent_run.jsonl", "agent_run.jsonl.1"):
                path = root / "meetingvar" / "agent_memory" / "trace" / name
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict) or event.get("event") != "llm_call":
                        continue
                    def number(key):
                        value = event.get(key)
                        return value if type(value) is int and value >= 0 else 0
                    prompt, completion = number("prompt_tokens"), number("completion_tokens")
                    reasoning = number("reasoning_tokens")
                    tokens = prompt + completion
                    stamp = event.get("ts")
                    try:
                        key = time.strftime("%Y-%m-%d", time.localtime(float(stamp)))
                    except (TypeError, ValueError, OSError):
                        key = date.today().isoformat()
                    day = days.setdefault(key, {"date": key, "tokens": 0, "calls": 0})
                    day["tokens"] += tokens
                    day["calls"] += 1
                    model = str(event.get("model") or "unknown")[:120]
                    row = models.setdefault(model, {"model": model, "tokens": 0, "calls": 0})
                    row["tokens"] += tokens
                    row["calls"] += 1
                    totals["tokens"] += tokens
                    totals["prompt_tokens"] += prompt
                    totals["completion_tokens"] += completion
                    totals["reasoning_tokens"] += reasoning
                    totals["calls"] += 1
                    if event.get("ok") is False:
                        totals["errors"] += 1
        end = date.today()
        series = []
        for offset in range(89, -1, -1):
            key = (end - timedelta(days=offset)).isoformat()
            series.append(days.get(key, {"date": key, "tokens": 0, "calls": 0}))
        return {"ok": True, "totals": totals, "days": series,
                "models": sorted(models.values(), key=lambda row: -row["tokens"]),
                "projects": [str(root) for root in roots]}

    def session_detail(self, sid: str) -> dict:
        data = Path(self._cfg["repo_dir"] or ".") / "data"
        detail = {"sid": sid, "minutes": "", "reports": "", "clips": []}
        for key, fname in (("minutes", "minutes.md"), ("reports", "var_reports.md")):
            f = data / "reports" / sid / fname
            if f.exists():
                try:
                    detail[key] = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        pool = data / "pool" / sid
        if pool.exists():
            for f in sorted(pool.glob("*.json")):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if "trigger" not in d:
                    continue
                hd_url = None
                hd_dir = (d.get("screen") or {}).get("hd_clip_dir")
                if hd_dir:
                    hd_mp4 = Path(hd_dir) / "hd_clip.mp4"
                    if not hd_mp4.is_absolute():
                        hd_mp4 = Path(self._cfg["repo_dir"]) / hd_mp4
                    if hd_mp4.exists():
                        hd_url = self._media_url(str(hd_mp4))
                audio = d.get("audio") or {}
                frames = []
                media_dir = data / "clips" / str(d.get("clip_id", ""))
                manifest = media_dir / "screens" / "manifest.txt"
                try:
                    for row in manifest.read_text(encoding="utf-8").splitlines():
                        name, timestamp = row.split("\t", 1)
                        if Path(name).name != name:
                            continue
                        url = self._media_url(str(media_dir / "screens" / name))
                        if url:
                            frames.append({"ts": float(timestamp), "url": url})
                except (OSError, ValueError):
                    pass
                frames.sort(key=lambda frame: frame["ts"])
                video_path = (d.get("screen") or {}).get("video_path")
                detail["clips"].append({
                    "clip_id": d.get("clip_id", "")[:8],
                    "layer": (d.get("trigger") or {}).get("layer", ""),
                    "evidence": (d.get("trigger") or {}).get("evidence", ""),
                    "at": (d.get("audio") or {}).get("end_ts", 0),
                    "lines": len(d.get("transcript", [])),
                    "audio_url": self._media_url((d.get("audio") or {}).get("path")),
                    "hd_url": hd_url,
                    "video_url": self._media_url(video_path),
                    "frames": frames,
                    "start_ts": audio.get("start_ts", 0),
                    "end_ts": audio.get("end_ts", 0),
                })
        return detail

    def digest_session(self, session_id: str, folder: str = "") -> dict:
        """Generate minutes at the engine mutation boundary, with a durable journal ID."""
        if not self._agent_valid_session(session_id):
            return {"ok": False, "error": "会话 ID 无效"}
        project = folder or self._session_projects.get(session_id, "")
        known = self._session_projects.get(session_id)
        if not project or not Path(project).is_dir():
            return {"ok": False, "error": "请先选择会议所属项目"}
        if known and Path(known).resolve() != Path(project).resolve():
            return {"ok": False, "error": "会议不属于所选项目"}
        return self._agent_bridge(["digest_session", project, session_id], use_llm=True, timeout=300)

    # ---- 本地 LLM 服务管理（llama.cpp，127.0.0.1:LLM_PORT）----
    def _local_startup_error(self) -> str:
        from local_llm import safe_startup_error
        try:
            with LLM_LOG_FILE.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(self._llm_log_offset, size - 16384))
                text = stream.read(16384).decode("utf-8", "replace")
            return safe_startup_error(text)
        except OSError:
            return "本地模型进程已退出，无法读取启动日志"

    def llm_status(self) -> dict:
        import urllib.request
        managed = self._llm_proc is not None and self._llm_proc.poll() is None
        result = {"up": False, "model": "", "managed": managed,
                  "gguf": self._cfg.get("llm_gguf", ""), "state": "loading" if managed else "stopped",
                  "backend": self._llm_launch.get("backend", ""), "error": self._llm_error}
        if self._llm_proc is not None and not managed:
            self._llm_error = self._llm_error or self._local_startup_error()
            result.update(state="error", error=self._llm_error)
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(f"http://127.0.0.1:{LLM_PORT}/v1/models", timeout=1) as response:
                data = json.loads(response.read())
            model = (data.get("data") or [{}])[0].get("id", "")
            if not isinstance(model, str) or not model:
                return result
            context = self._llm_launch.get("context_tokens", min(int(self._cfg.get("llm_context_tokens", 16384)), 8192))
            if not self._llm_launch:
                try:
                    with op.open(f"http://127.0.0.1:{LLM_PORT}/props", timeout=1) as response:
                        props = json.loads(response.read())
                    context = int(props.get("default_generation_settings", {}).get("n_ctx") or context)
                except (OSError, ValueError, TypeError):
                    pass
            result.update(up=True, model=model, display_model=Path(model).name, state="ready", error="", context_tokens=context)
            return result
        except (OSError, ValueError, TypeError, IndexError):
            return result

    def llm_start(self) -> dict:
        from local_llm import build_launch
        if not self._llm_start_lock.acquire(blocking=False):
            return {"ok": True, "starting": True, "note": "已有启动请求正在处理"}
        try:
            state = self.llm_status()
            if state["up"]:
                result = self._test_provider("local")
                result.update(already=True, model=state["model"])
                return result
            if state["managed"]:
                return {"ok": True, "starting": True}
            try:
                launch = build_launch(self._cfg, LLM_PORT)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self._llm_log_offset = LLM_LOG_FILE.stat().st_size if LLM_LOG_FILE.exists() else 0
            self._llm_error = ""
            self._llm_launch = launch
            try:
                with LLM_LOG_FILE.open("a", encoding="utf-8") as logf:
                    self._llm_proc = subprocess.Popen(launch["cmd"], cwd=self._cfg.get("repo_dir") or None,
                        stdout=logf, stderr=subprocess.STDOUT, env=self._engine_env(), creationflags=_SPAWN_FLAGS)
            except OSError:
                self._llm_error = "本地模型进程启动失败，请检查推理后端和文件权限"
                return {"ok": False, "error": self._llm_error}
            log(f"[ui] LLM 服务启动中 pid={self._llm_proc.pid} backend={launch['backend']} ctx={launch['context_tokens']}")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                state = self.llm_status()
                if state["up"]:
                    result = self._test_provider("local")
                    result.update(model=state["model"], backend=launch["backend"], context_tokens=launch["context_tokens"])
                    return result
                if not state["managed"]:
                    return {"ok": False, "error": state.get("error") or "本地模型启动后异常退出"}
                time.sleep(0.25)
            return {"ok": True, "starting": True, "note": "模型仍在加载，尚未通过生成验证"}
        finally:
            self._llm_start_lock.release()

    def llm_stop(self) -> dict:
        st = self.llm_status()
        if not st["up"] and not st["managed"]:
            return {"ok": False, "error": "服务未在运行"}
        if not st["managed"]:
            return {"ok": False, "error": "该服务不是本应用启动的（可能在别的终端里），未终止它"}
        try:
            self._llm_proc.terminate()
            try:
                self._llm_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "本地服务正在停止，请稍后检查状态"}
            self._llm_proc = None
            self._llm_error = ""
            log("[ui] LLM 服务已终止")
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # ---- 讯飞识别（设置检测 + 文件转写工具）----
    def check_xfyun(self) -> dict:
        """检测讯飞文件夹：认领凭证 + 语音听写静音握手探活（不打印密钥）。"""
        xdir = (self._cfg.get("xfyun_dir") or "").strip()
        if not xdir or not Path(xdir).exists():
            return {"ok": False, "error": "未配置讯飞文件夹（设置页选择讯飞控制台下载目录）"}
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        try:
            r = subprocess.run(
                [py, str(bridge_path()), "check_xfyun", xdir],
                cwd=self._cfg.get("repo_dir") or None, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
                env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "检测超时（>60s）"}
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return {"ok": False, "error": f"引擎无输出（rc={r.returncode}）"}
        try:
            return json.loads(out[-1])
        except ValueError:
            return {"ok": False, "error": f"输出解析失败: {out[-1][:120]}"}

    def _test_provider(self, provider: str) -> dict:
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).is_file():
            return {"ok": False, "error_code": "configuration", "error": "未配置引擎Python"}
        try:
            effective = self._llm_env(provider=provider, purpose="reading")
        except ValueError as exc:
            return {"ok": False, "error_code": "configuration", "error": str(exc)}
        env = self._engine_env()
        env.update(effective)
        try:
            process = subprocess.run([py, str(bridge_path()), "test_llm"], cwd=self._cfg.get("repo_dir") or None,
                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=150, creationflags=_SPAWN_FLAGS)
            lines = [line for line in process.stdout.splitlines() if line.startswith("{")]
            result = json.loads(lines[-1]) if lines else {"ok": False, "error_code": "empty_response", "error": "测试进程没有返回结果"}
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error_code": "timeout", "error": "生成验证超时，尚未证明服务可用"}
        except (OSError, ValueError, TypeError):
            result = {"ok": False, "error_code": "invalid_response", "error": "生成验证失败或返回格式无效"}
        result["provider"] = provider
        fingerprint = self._llm_fingerprint(effective)
        if result.get("ok") is True:
            self._llm_verified.add(fingerprint)
            self._llm_verified_at[fingerprint] = time.time()
            result.update(note="已通过实际JSON生成验证", models=[effective["MEETINGVAR_LLM_MODEL"]])
        else:
            self._llm_verified.discard(fingerprint)
            self._llm_verified_at.pop(fingerprint, None)
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / "llm_validation.json").write_text(json.dumps(self._llm_verified_at), encoding="utf-8")
        except OSError:
            pass
        return result

    def test_llm(self) -> dict:
        return self._test_provider(self._cfg.get("llm_provider") or "local")

    def model_status(self) -> dict:
        """Expose readiness reasons without returning URL, key or other secrets."""
        return self._provider_info()

    def pick_media(self):
        """选一个媒体文件（视频/音频）用于离线转写。"""
        import webview

        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("媒体文件 (*.mp4;*.mov;*.mkv;*.avi;*.wav;*.mp3;*.m4a;*.flac;*.aac)", "所有文件 (*.*)"),
            )
        except Exception as exc:  # noqa: BLE001
            log(f"[ui] 文件对话框失败: {exc}")
            return None
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    def transcribe_media_start(self, path: str) -> dict:
        """后台起"文件转写"（ffmpeg 抽音 → 讯飞录音文件转写 → 写 <stem>.transcript.txt）。"""
        if self._tf.get("running"):
            return {"ok": False, "error": "已有转写任务在跑"}
        xdir = (self._cfg.get("xfyun_dir") or "").strip()
        if not xdir or not Path(xdir).exists():
            return {"ok": False, "error": "未配置讯飞文件夹（设置页 → 识别引擎）"}
        if not path or not Path(path).exists():
            return {"ok": False, "error": "文件不存在"}
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        operation=self._host_operation_begin(self._current_project,"host.transcribe_media",{"path":path})
        if not operation.get("ok"):
            return operation
        self._tf = {"operation":operation,"running": True, "file": path, "progress": "启动…", "done": False,
                    "ok": False, "out": "", "error": ""}
        try:
            proc = subprocess.Popen(
                [py, str(bridge_path()), "transcribe_file", path, xdir],
                cwd=self._cfg.get("repo_dir") or None, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except OSError as exc:
            self._tf.update({"running": False, "done": True, "error": f"启动失败: {exc}"})
            return self._host_operation_finish(operation,{"ok":False,"error":type(exc).__name__})
        threading.Thread(target=self._tf_reader, args=(proc,), daemon=True).start()
        log(f"[ui] 文件转写任务启动: {path}")
        return {"ok": True, "file": Path(path).name, "operation_id":operation["operation_id"],
                "operation_status":"running"}

    def _tf_reader(self, proc: subprocess.Popen) -> None:
        final: dict | None = None
        try:
            for line in proc.stdout or []:
                line = line.rstrip("\r\n")
                if line.startswith("[progress] "):
                    self._tf["progress"] = line[len("[progress] "):]
                elif line.startswith("{"):
                    final = line
        except Exception as exc:  # noqa: BLE001
            self._tf.update({"error": f"读取输出失败: {exc}"})
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        if final:
            try:
                data = json.loads(final)
                self._tf.update({"ok": bool(data.get("ok")), "out": data.get("out", ""),
                                 "error": data.get("error", "") or "",
                                 "progress": "完成" if data.get("ok") else "失败"})
            except ValueError:
                self._tf.update({"error": "结果解析失败"})
        elif not self._tf.get("error"):
            self._tf.update({"error": f"引擎退出（rc={proc.returncode}）且无结果"})
        operation=self._tf.get("operation")
        if operation:
            receipt=self._host_operation_finish(operation,{k:self._tf.get(k) for k in ("ok","out","error","file")})
            self._tf.update({k:receipt[k] for k in ("ok","error","operation_id","operation_status","journal_pending") if k in receipt})
        self._tf["running"] = False
        self._tf["done"] = True
        log(f"[ui] 文件转写结束: ok={self._tf['ok']} out={self._tf['out'] or '-'} err={self._tf['error'] or '-'}")
        self._push("transcribe_done", **{k: self._tf[k] for k in ("ok", "out", "error", "file")})

    # ---- 全库理解（分层阅读整个文件夹；厂商由设置页决定，本地=零出网）----
    def understand_plan(self, folder: str) -> dict:
        """零 LLM 预估：字数/块数/本地耗时 + 当前厂商与出网提示（开始前给用户看）。"""
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        try:
            env = self._engine_env()
            env.update(self._llm_env())
        except ValueError as exc:
            return {"ok": False, "error": str(exc), **self._provider_info()}
        try:
            r = subprocess.run(
                [py, str(bridge_path()), "understand_plan", folder],
                cwd=self._cfg.get("repo_dir") or None, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800,
                env=env, creationflags=_SPAWN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "预估超时（>120s）"}
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return {"ok": False, "error": f"引擎无输出（rc={r.returncode}）"}
        try:
            data = json.loads(out[-1])
        except ValueError:
            return {"ok": False, "error": f"输出解析失败: {out[-1][:120]}"}
        data.update(self._provider_info())
        return data

    def understand_start(self, folder: str, max_chunks: int = 0) -> dict:
        with self._und_lock:
            return self._understand_start(folder, max_chunks)

    def _understand_start(self, folder: str, max_chunks: int = 0,
                          action_context: tuple[str, str, str] | None = None) -> dict:
        """后台开跑全库理解；进度 [progress] 行；可随时取消（已完成块有缓存，续跑不重复）。"""
        if self._und.get("running"):
            return {"ok": False, "error": "已有全库理解任务在跑"}
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        info = self._provider_info()
        if not info["ready"]:
            return {"ok": False, "error": info["error"], "error_code": "not_verified"}
        self._und = {"running": True, "done": False, "ok": False, "folder": folder,
                     "progress": "启动…", "result": {}, "error": "", "max_chunks": max_chunks,
                     "operation_id": action_context[2] if action_context else str(uuid4())}
        cmd = [py, str(bridge_path()), "understand", folder] + ([str(max_chunks)] if max_chunks else [])
        try:
            env = self._engine_env()
            env.update(self._llm_env())  # 全库理解必须有模型：本地服务/自定义端点在此校验
            if action_context:
                env["MEETINGVAR_CHAT_ACTION_ID"] = action_context[2]
            else:
                env["MEETINGVAR_OPERATION_ID"] = self._und["operation_id"]
        except ValueError as exc:
            self._und.update({"running": False, "done": True, "error": str(exc)})
            return {"ok": False, "error": str(exc)}
        try:
            proc = subprocess.Popen(
                cmd, cwd=self._cfg.get("repo_dir") or None, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=env, creationflags=_SPAWN_FLAGS,
            )
        except OSError as exc:
            self._und.update({"running": False, "done": True, "error": f"启动失败: {exc}"})
            return {"ok": False, "error": str(exc)}
        self._und_proc = proc
        threading.Thread(target=self._und_reader, args=(proc, self._und, action_context), daemon=True).start()
        log(f"[ui] 全库理解任务启动: {folder} max_chunks={max_chunks or '全量'}")
        return {"ok": True, "folder": folder, "operation_id": self._und["operation_id"]}

    def _und_reader(self, proc: subprocess.Popen, task: dict | None = None,
                    action_context: tuple[str, str, str] | None = None) -> None:
        task = self._und if task is None else task
        final: str | None = None
        try:
            for line in proc.stdout or []:
                line = line.rstrip("\r\n")
                if line.startswith("[progress] "):
                    task["progress"] = line[len("[progress] "):]
                elif line.startswith("{"):
                    final = line
        except Exception as exc:  # noqa: BLE001
            task["error"] = f"读取输出失败: {exc}"
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
        if final:
            try:
                data = json.loads(final)
                partial = bool(data.get("failed_chunks")) and int(data.get("chunks_read") or 0) > 0
                task.update({"ok": data.get("ok") is True and proc.returncode == 0
                              and not task.get("cancelled") and data.get("complete") is not False,
                              "result": data, "partial": partial,
                                  "error": data.get("error") or "", "error_code": data.get("error_code"),
                                  "warnings": data.get("warnings") or [],
                                  "progress": "完成" if data.get("ok") else
                                              ("部分完成" if partial else "中断")})
            except ValueError:
                task["error"] = "结果解析失败"
        elif not task.get("error"):
            rc = proc.returncode
            task["error"] = ("已取消（成功块缓存保留）" if task.get("cancelled")
                                  else f"引擎异常退出（代码{rc}），未返回诊断结果")
        task["running"] = False
        task["done"] = True
        with self._und_lock:
            if self._und_proc is proc:
                self._und_proc = None
        log(f"[ui] 全库理解结束: ok={task['ok']} err={task['error'] or '-'}")
        result = task.get("result") or {}
        self._push("understand_done", ok=task["ok"], error=task["error"],
                   partial=bool(task.get("partial")), folder=task["folder"],
                   chunks_read=result.get("chunks_read", 0),
                   chunks_total=result.get("chunks_total", 0),
                   coverage_pct=result.get("coverage_pct", 0.0))

        if action_context:
            receipt = {"ok": task.get("ok") is True and not task.get("cancelled"),
                       "result": task.get("result") or {},
                       "error": task.get("error") or ""}
            finalized = self._finalize_chat_action(*action_context, receipt)
            task["action_finalized"] = finalized.get("status") in {"done", "succeeded", "failed"}

    def understand_status(self) -> dict:
        return dict(self._und)

    def understand_cancel(self) -> dict:
        proc = self._und_proc
        if proc is None or proc.poll() is not None:
            return {"ok": False, "error": "没有在跑的任务"}
        try:
            self._und["cancelled"] = True
            proc.terminate()
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def terms_write(self, folder: str, terms: list[str]) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        words = [str(term).strip() for term in (terms or []) if str(term).strip()]
        if not words:
            return {"ok": False, "error": "没有勾选任何词"}
        payload = self._agent_payload_file(words)
        try:
            return self._agent_bridge(["write_terms", folder, str(payload)], timeout=60)
        finally:
            try:
                payload.unlink()
            except OSError:
                pass

    def operation_get(self, folder: str, operation_id: str) -> dict:
        if not folder or not Path(folder).is_dir() or not self._agent_valid_session(operation_id):
            return {"ok": False, "error": "项目或操作 ID 无效"}
        return self._agent_bridge(["operation_get", folder, operation_id], timeout=30)

    def transcribe_media_status(self) -> dict:
        return dict(self._tf)
    def list_library(self) -> list[dict]:
        sessions = {s["sid"]: s for s in self.list_sessions()}
        mapping = dict(self._session_projects)
        # 归档扫描补历史映射：<项目>/meetingvar/minutes/<session_id>/
        for proj in self._cfg.get("projects") or []:
            md = Path(proj) / "meetingvar" / "minutes"
            try:
                if md.is_dir():
                    for d in md.iterdir():
                        if d.is_dir():
                            mapping.setdefault(d.name, proj)
            except OSError:
                continue
        groups: dict[str, list] = {}
        for sid, s in sessions.items():
            groups.setdefault(mapping.get(sid, ""), []).append(s)
        out = []
        for proj, ss in groups.items():
            ss.sort(key=lambda x: -x["mtime"])
            out.append({
                "project": proj,
                "name": Path(proj).name if proj else "（未归属会话）",
                "sessions": ss,
                "latest": ss[0]["mtime"] if ss else 0,
            })
        out.sort(key=lambda g: -g["latest"])
        return out

    # ---- 系统动作 / 配置 ----
    def open_path(self, path: str) -> dict:
        p = path or self._cfg.get("repo_dir") or ""
        try:
            if p and Path(p).exists():
                os.startfile(p)  # type: ignore[attr-defined]
                return {"ok": True}
            return {"ok": False, "error": "路径不存在"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def open_data_dir(self) -> dict:
        """「打开产物文件夹」：优先当前项目的 meetingvar 归档（纪要/名册），否则引擎 data 池。"""
        candidates = []
        proj = self._cfg.get("last_project") or ""
        if proj:
            candidates.append(Path(proj) / "meetingvar")
        repo = Path(self._cfg.get("repo_dir") or ".")
        candidates += [repo / "data", repo]
        for c in candidates:
            if c.exists():
                return self.open_path(str(c))
        return {"ok": False, "error": "没有可打开的目录"}

    def open_config_dir(self) -> dict:
        """「打开配置目录」：%APPDATA%\\meetingVAR（配置/日志/会话映射都在此）。"""
        return self.open_path(str(CONFIG_DIR))

    def shutdown(self) -> None:
        """窗口关闭后的收尾：优雅停会议（最多等 30s）→ 终止本应用启动的 LLM 服务。

        必要性：Windows 子进程不随父进程退出——不清理的话关窗后引擎继续录音、
        llama.cpp 继续占内存（实测确认无 atexit/on_closing 时的孤儿行为）。
        """
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if self._send_control(":stop"):
                    log("[ui] 退出：已发送优雅停止（控制文件）")
                elif proc.stdin:
                    proc.stdin.write(":stop\n")
                    proc.stdin.flush()
                    log("[ui] 退出：已发送优雅停止（:stop）")
                try:
                    proc.wait(timeout=30)  # 收尾含 digest+归档，给足时间
                except subprocess.TimeoutExpired:
                    log("[ui] 退出：引擎 30s 未退出，终止")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:  # noqa: BLE001 — 退出路径不许抛
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
        if self._llm_proc is not None and self._llm_proc.poll() is None:
            try:
                self._llm_proc.terminate()
                log("[ui] 退出：LLM 服务已终止")
            except Exception:  # noqa: BLE001
                pass

    def _tray_show(self, icon=None, item=None) -> None:
        window = self._window
        if window is None:
            return
        try:
            window.show()
            try:
                window.restore()
            except Exception:  # noqa: BLE001 - older pywebview builds
                pass
        except Exception as exc:  # noqa: BLE001
            log(f"[tray] 显示窗口失败：{exc}")

    def _tray_hide(self, icon=None, item=None) -> None:
        window = self._window
        if window is None:
            return
        try:
            window.hide()
        except Exception as exc:  # noqa: BLE001
            log(f"[tray] 隐藏窗口失败：{exc}")

    def _hide_to_tray(self) -> None:
        self._tray_hide()
        icon = self._tray_icon
        if icon is not None and not self._tray_notified:
            self._tray_notified = True
            try:
                icon.notify("meetingVAR 仍在后台运行。双击托盘图标打开，右键可退出。",
                            "meetingVAR")
            except Exception:  # noqa: BLE001 - notifications are best effort
                pass
        log("[ui] 窗口已隐藏到托盘")

    def _quit_from_tray(self, icon=None, item=None) -> None:
        """Tray quit must not call window.destroy() from a worker thread (WinForms
        deadlocks); stop the engine/LLM, remove the icon, then exit the process."""
        self._tray_quit = True

        def _finish() -> None:
            try:
                if icon is not None:
                    icon.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.shutdown()
            except Exception:  # noqa: BLE001
                pass
            os._exit(0)

        threading.Thread(target=_finish, daemon=True).start()

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as exc:  # noqa: BLE001 - app still works without tray
            log(f"[tray] 不可用：{type(exc).__name__}: {exc}")
            return
        image = None
        for candidate in (ASSETS_DIR / "icon.ico", ASSETS_DIR / "icon.png"):
            try:
                if candidate.is_file():
                    image = Image.open(candidate).convert("RGBA")
                    break
            except Exception:  # noqa: BLE001
                continue
        if image is None:
            image = Image.new("RGBA", (64, 64), (201, 255, 105, 255))
            draw = ImageDraw.Draw(image)
            draw.ellipse((8, 8, 56, 56), fill=(18, 20, 17, 255))
        menu = pystray.Menu(
            pystray.MenuItem("打开 meetingVAR", self._tray_show, default=True),
            pystray.MenuItem("隐藏到托盘", self._tray_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 meetingVAR", self._quit_from_tray),
        )
        try:
            icon = pystray.Icon("meetingVAR", image, f"meetingVAR {APP_VERSION}", menu)
            icon.run_detached()
            self._tray_icon = icon
            self._tray_available = True
            log("[ui] 系统托盘图标已启动")
        except Exception as exc:  # noqa: BLE001
            log(f"[tray] 启动失败：{type(exc).__name__}: {exc}")

    def _allow_close(self) -> bool:
        if self._tray_quit:
            return True
        if not self.is_running():
            if self._tray_available:
                self._hide_to_tray()
                return False
            return True
        if self._capture_phase != "stopping":
            self.stop_meeting()
        self._capture_phase = "stopping"
        self._push("capture_state", phase="stopping", text="正在保存会议，请等待收尾完成后关闭窗口")
        return False

    def license_status(self) -> dict:
        from meetingvar.commercial.license import status

        return {"ok": True, **status(self._cfg.get("license_token", ""),
                                     self._cfg.get("trial_started", ""))}

    def license_activate(self, token: str) -> dict:
        from meetingvar.commercial.license import verify_license

        verified = verify_license(token)
        if not verified.get("valid"):
            return {"ok": False, "error": verified.get("reason", "许可证无效")}
        self._cfg["license_token"] = str(token).strip()
        save_config(self._cfg)
        return self.license_status()

    def license_remove(self) -> dict:
        self._cfg["license_token"] = ""
        save_config(self._cfg)
        return self.license_status()

    def update_check(self) -> dict:
        from meetingvar.commercial.updates import check_manifest

        return check_manifest(self._cfg.get("update_manifest_url", ""), APP_VERSION)

    def update_download(self, url: str, sha256: str) -> dict:
        from meetingvar.commercial.updates import download_update

        result = download_update(url, sha256)
        if result.get("ok"):
            self._update_path = result["path"]
        return result

    def update_install(self) -> dict:
        path = Path(getattr(self, "_update_path", ""))
        if not path.is_file() or path.suffix.lower() != ".exe":
            return {"ok": False, "error": "没有已验证的更新包"}
        try:
            subprocess.Popen([str(path), "--silent", "--dir", str(_APP_HOME)],
                             creationflags=_SPAWN_FLAGS)
        except OSError as exc:
            return {"ok": False, "error": f"更新启动失败: {exc}"}
        return {"ok": True, "path": str(path)}

    def telemetry_set(self, enabled: bool) -> dict:
        self._cfg["telemetry_enabled"] = bool(enabled)
        save_config(self._cfg)
        return {"ok": True, "enabled": self._cfg["telemetry_enabled"]}

    def crash_reports_open(self) -> dict:
        target = CONFIG_DIR / "crashes"
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError:
            return {"ok": False, "error": "无法打开崩溃报告目录"}
        return {"ok": True, "path": str(target)}

    def open_legal(self, name: str) -> dict:
        files = {"privacy": "PRIVACY.md", "terms": "TERMS.md",
                 "licenses": "THIRD_PARTY_LICENSES.md"}
        filename = files.get(str(name).lower())
        if not filename:
            return {"ok": False, "error": "未知文档"}
        for base in (_BASE / "legal", Path(__file__).resolve().parents[1] / "docs"):
            path = base / filename
            if path.is_file():
                try:
                    os.startfile(str(path))  # type: ignore[attr-defined]
                except OSError:
                    return {"ok": False, "error": "无法打开文档"}
                return {"ok": True, "path": str(path)}
        return {"ok": False, "error": "文档未随安装包分发"}

    def data_inventory(self, folder: str = "") -> dict:
        from meetingvar.commercial.data import inventory

        data_dir = Path(self._cfg.get("repo_dir") or _APP_HOME) / "data"
        return inventory(folder or self._cfg.get("last_project", ""), data_dir)

    def export_project_data(self, folder: str, out_dir: str) -> dict:
        from meetingvar.commercial.data import export_project

        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目目录无效"}
        if not out_dir or not Path(out_dir).is_dir():
            return {"ok": False, "error": "导出目录无效"}
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = Path(out_dir) / f"meetingvar-export-{stamp}.zip"
        source = (Path(folder) / "meetingvar").resolve()
        if source == target.resolve().parent or source in target.resolve().parents:
            return {"ok": False, "error": "导出目录不能位于待导出的 meetingvar 数据目录内"}
        return self._host_operation_run(folder, "host.export_project_data",
                                        {"target": str(target)},
                                        lambda: export_project(folder, target))

    def delete_project_data(self, folder: str, categories: list, confirm: str) -> dict:
        from meetingvar.commercial.data import CATEGORIES, delete_categories

        if not isinstance(categories, list) or any(not isinstance(key, str) for key in categories):
            return {"ok": False, "error": "删除类别无效"}
        if str(confirm or "").strip() != "DELETE":
            return {"ok": False, "error": "请输入 DELETE 以确认删除"}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目目录无效"}
        data_dir = Path(self._cfg.get("repo_dir") or _APP_HOME) / "data"
        selected = list(dict.fromkeys(key for key in categories
                                      if key in CATEGORIES or key in {"clips", "pool", "reports"}))
        if not selected:
            return {"ok": True, "removed": [], "count": 0}
        operation = self._host_operation_begin(folder, "host.delete_project_data",
                                                {"categories": selected})
        if operation.get("ok") is not True:
            return operation
        targets = [Path(folder) / rel for key in selected if key in CATEGORIES
                   for rel in CATEGORIES[key][1]]
        targets.extend(data_dir / key for key in selected if key in {"clips", "pool", "reports"})
        try:
            receipt = delete_categories(folder, data_dir, selected, confirm)
            remaining = [str(path) for path in targets if path.exists()]
            if remaining:
                receipt = {**receipt, "ok": False, "remaining": remaining,
                           "error": "部分所选数据未删除，请核对权限和占用"}
        except Exception:
            receipt = {"ok": False, "error": "删除结果未能确认", "outcome_unknown": True}
        journal = Path(folder) / "meetingvar" / "agent_memory" / "operations.sqlite3"
        if "agent" in selected and not journal.is_file():
            # Full deletion includes operation history. Never resurrect it or keep a hidden copy.
            return {**receipt, "operation_id": operation["operation_id"],
                    "operation_status": "succeeded" if receipt.get("ok") is True else "interrupted",
                    "erased_own_journal": True, "durable_receipt": False,
                    "outcome_unknown": receipt.get("ok") is not True,
                    "warning": "所选 Agent 数据包含操作历史，已一并删除；本回执不持久保存"}
        return self._host_operation_finish(operation, receipt)

    def get_config(self) -> dict:
        return self._cfg

    def set_config(self, updates: dict) -> dict:
        for k, default in CONFIG_DEFAULTS.items():
            if k not in updates:
                continue
            val = updates[k]
            if isinstance(default, bool):
                self._cfg[k] = bool(val)
            elif isinstance(default, list):
                if isinstance(val, list):
                    self._cfg[k] = [str(x) for x in val]
            elif isinstance(default, int):
                if isinstance(val, int) and not isinstance(val, bool):
                    self._cfg[k] = val
            elif isinstance(val, str):
                self._cfg[k] = val
        save_config(self._cfg)
        return self._cfg

    def test_engine(self) -> dict:
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "引擎 Python 路径不存在"}
        try:
            r = subprocess.run([py, str(bridge_path()), "check"], cwd=repo,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, env=self._engine_env(),
                               creationflags=_SPAWN_FLAGS)
            out = (r.stdout or "").strip().splitlines()
            if out:
                data = json.loads(out[-1])
                if data.get("ok"):
                    data["bridge"] = str(bridge_path())
                return data
            return {"ok": False, "error": f"无输出 rc={r.returncode}: {(r.stderr or '')[-200:]}"}
        except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---- Agent 桌面接口（桥接到引擎 Python） ----
    def _agent_bridge(self, args: list[str], *, use_llm: bool = False, timeout: int = 300) -> dict:
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if not py or not Path(py).is_file():
            return {"ok": False, "error": "未配置引擎 Python"}
        env = self._engine_env()
        mutations = {"understand", "write_terms", "agent_focus_save", "agent_facts_save",
                     "agent_memory_update", "agent_memory_delete", "agent_memory_pin", "agent_memory_resolve",
                     "rag_index", "kg_add_node", "kg_add_edge", "kg_delete_node", "kg_delete_edge",
                     "kg_build", "kg_extract_project", "kg_ingest", "kg_merge", "kg_review_edge", "digest_session",
                     "architecture_generate", "architecture_save"}
        operation_id = str(uuid4()) if args and args[0] in mutations else ""
        identity = {"operation_id": operation_id, "run_id": operation_id} if operation_id else {}
        if operation_id:
            env["MEETINGVAR_OPERATION_ID"] = operation_id
            # These mutations run wholly in this bridge. A killed bridge must
            # become recoverable even while the desktop stays alive.
            env.pop("MEETINGVAR_OPERATION_OWNER_PID", None)
            env.pop("MEETINGVAR_OPERATION_OWNER_START", None)
        if args and args[0] == 'workspace_start':
            env['MEETINGVAR_WORKSPACE_MODEL_READY'] = '0'
            # Indexing must remain available without a model. Never switch providers.
            info = self._provider_info()
            log(f"[workspace] model provider={info.get('provider')} configured={info.get('configured')} "
                f"ready={info.get('ready')} error={str(info.get('error') or '')[:160]}")
            if info.get('ready'):
                try:
                    env.update(self._llm_env(purpose='reading'))
                    env['MEETINGVAR_WORKSPACE_MODEL_READY'] = '1'
                except ValueError:
                    pass  # The durable job explicitly reports awaiting_model.
        elif use_llm:
            try:
                env.update(self._llm_env(purpose="reading"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc), **identity}
        try:
            process = subprocess.run(
                [py, str(bridge_path()), *args], cwd=repo, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=_SPAWN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Agent 任务超时；请查询原操作并核对副作用，不要直接重复写入",
                    "recovery_required": bool(operation_id), **identity}
        except OSError:
            return {"ok": False, "error": "Agent 子进程启动失败", **identity}
        lines = [line for line in (process.stdout or "").splitlines() if line.startswith("{")]
        if not lines:
            return {"ok": False, "error": "Agent 没有返回结构化结果", "recovery_required": bool(operation_id), **identity}
        try:
            data = json.loads(lines[-1])
        except ValueError:
            return {"ok": False, "error": "Agent 结果无法解析", **identity}
        return {**data, **identity} if isinstance(data, dict) else {"ok": False, "error": "Agent 结果格式无效", **identity}

    def _agent_bridge_stream(self, args: list[str], *, use_llm: bool = False,
                             timeout: int = 300, identity: dict | None = None) -> dict:
        """Own one explicit run until its reader and process have both ended."""
        identity = dict(identity or {})
        if not all(identity.get(key) for key in ("folder", "conversation_id", "run_id")):
            return {"ok": False, "error": "缺少聊天运行身份"}
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if not py or not Path(py).is_file():
            return {"ok": False, "error": "未配置引擎 Python", **identity}
        env = self._engine_env()
        env["MEETINGVAR_DESKTOP_EVENTS"] = "1"
        if use_llm:
            try:
                env.update(self._llm_env(purpose="reading"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc), **identity}
        with self._chat_lock:
            if self._chat_identity is not None:
                return {"ok": False, "error_code": "chat_busy",
                        "error": "已有回答正在生成，请先停止该运行", **identity}
            if identity["run_id"] in self._chat_seen_runs:
                return {"ok": False, "error_code": "duplicate_run",
                        "error": "运行 ID 已使用，请使用新的 UUID", **identity}
            try:
                process = subprocess.Popen(
                    [py, str(bridge_path()), *args], cwd=repo, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=_SPAWN_FLAGS,
                )
            except OSError:
                return {"ok": False, "error": "Agent 子进程启动失败", **identity}
            self._chat_proc = process
            self._chat_identity = identity
            self._chat_seen_runs.add(identity["run_id"])
            self._chat_cancelled = False
        final_line: list[str] = []

        def reader() -> None:
            for line in process.stdout or []:
                if line.startswith("@@MEETINGVAR_EVENT "):
                    try:
                        event = json.loads(line[len("@@MEETINGVAR_EVENT "):])
                        if isinstance(event, dict) and event.get("type") == "agent_progress":
                            event.pop("type", None)
                            self._push("agent_progress", **{**event, **identity})
                    except (ValueError, TypeError):
                        pass
                elif line.startswith("{"):
                    final_line[:] = [line]

        thread = threading.Thread(target=reader, daemon=True)
        timed_out = False
        try:
            thread.start()
            thread.join(timeout)
            timed_out = thread.is_alive()
            if timed_out:
                process.kill()
            exit_code = process.wait(timeout=10)
            if timed_out:
                thread.join(2)
            with self._chat_lock:
                cancelled = self._chat_cancelled
            if cancelled or timed_out:
                reason = "cancelled" if cancelled else "timeout"
                journal = self._agent_bridge(["chat_interrupt", identity["folder"],
                                              identity["run_id"], reason], timeout=30)
                return {"ok": False, "error_code": reason,
                        "error": "已停止生成" if cancelled else "Agent 任务超时",
                        "journal_updated": journal.get("ok") is True, **identity}
            if exit_code != 0 or not final_line:
                reason = "process_exit_error" if exit_code != 0 else "process_exited_without_result"
                journal = self._agent_bridge(["chat_interrupt", identity["folder"],
                                              identity["run_id"], reason], timeout=30)
                return {"ok": False, "error_code": reason,
                        "error": "Agent 异常退出" if exit_code != 0 else "Agent 没有返回结构化结果",
                        "journal_updated": journal.get("ok") is True, **identity}
            try:
                data = json.loads(final_line[-1])
                if not isinstance(data, dict):
                    raise ValueError("invalid result")
            except ValueError:
                journal = self._agent_bridge(["chat_interrupt", identity["folder"],
                                              identity["run_id"], "invalid_result"], timeout=30)
                return {"ok": False, "error_code": "invalid_result", "error": "Agent 结果无法解析",
                        "journal_updated": journal.get("ok") is True, **identity}
            return {**data, **identity}
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "Agent 运行尚未确认退出，保留运行身份", **identity}
        finally:
            with self._chat_lock:
                # Never permit a second process to overwrite a still-live one.
                if self._chat_proc is process and process.poll() is not None:
                    self._chat_proc = None
                    self._chat_identity = None

    @staticmethod
    def _agent_valid_session(session: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", session or "")) and ".." not in session

    def _agent_payload_file(self, payload) -> Path:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="agent_", suffix=".json", dir=str(CONFIG_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            return Path(name)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def agent_status(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["agent_status", folder], timeout=60)

    def agent_focus_preview(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["agent_focus_preview", folder], use_llm=True, timeout=240)

    def agent_focus_save(self, folder: str, items: list, sources: list | None = None) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "没有选择关注点"}
        payload = self._agent_payload_file({"items": items, "sources": sources or []})
        try:
            return self._agent_bridge(["agent_focus_save", folder, str(payload)], timeout=60)
        finally:
            try:
                payload.unlink()
            except OSError:
                pass

    def agent_extract_preview(self, session: str, folder: str) -> dict:
        if not self._agent_valid_session(session):
            return {"ok": False, "error": "会话 ID 无效"}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["agent_extract_preview", session, folder],
                                  use_llm=True, timeout=300)

    def agent_facts_save(self, session: str, folder: str, items: list) -> dict:
        if not self._agent_valid_session(session):
            return {"ok": False, "error": "会话 ID 无效"}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "没有选择事实"}
        payload = self._agent_payload_file(items)
        try:
            return self._agent_bridge(["agent_facts_save", session, folder, str(payload)], timeout=120)
        finally:
            try:
                payload.unlink()
            except OSError:
                pass

    def agent_ask(self, folder: str, question: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(question, str) or not question.strip():
            return {"ok": False, "error": "请输入问题"}
        return self._agent_bridge(["agent_ask", folder, question.strip()[:800]],
                                  use_llm=True, timeout=180)

    def agent_run(self, folder: str, question: str, max_steps: int = 8,
                  mode: str = "auto") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(question, str) or not question.strip():
            return {"ok": False, "error": "请输入问题"}
        try:
            steps = max(1, min(12, int(max_steps)))
        except (TypeError, ValueError):
            steps = 8
        mode = mode if mode in {"auto", "native", "json"} else "auto"
        return self._agent_bridge(["agent_run", folder, question.strip()[:1000],
                                   str(steps), mode], use_llm=True, timeout=300)

    def agent_resume(self, folder: str, task_id: str, max_steps: int = 8) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(task_id):
            return {"ok": False, "error": "任务 ID 无效"}
        return self._agent_bridge(["agent_resume", folder, task_id, str(max_steps), "auto"],
                                  use_llm=True, timeout=300)

    def read_project_file(self, folder: str, relative: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(relative, str) or not relative.strip() or len(relative) > 1000:
            return {"ok": False, "error": "文件路径无效"}
        result = self._agent_bridge(["read_project_file", folder, relative], timeout=120)
        if result.get("ok") and result.get("kind") == "pdf":
            url = self.project_file_url(folder, relative)
            if url:
                result.pop("data_url", None)
                result["url"] = url
        return result

    def open_project_file(self, folder: str, relative: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(relative, str) or not relative.strip() or "\x00" in relative:
            return {"ok": False, "error": "文件路径无效"}
        root = Path(folder).resolve()
        rel = Path(relative.replace("\\\\", "/"))
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return {"ok": False, "error": "文件路径越界"}
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file() or target.is_symlink():
            return {"ok": False, "error": "文件不存在或不可打开"}
        try:
            os.startfile(str(target))
        except OSError as exc:
            return {"ok": False, "error": f"无法用系统应用打开：{exc}"}
        return {"ok": True, "path": str(target)}

    def chat_new(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["agent_chat_new", folder], timeout=30)

    def chat_list(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["agent_chat_list", folder], timeout=30)

    def chat_load(self, folder: str, conversation_id: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(conversation_id):
            return {"ok": False, "error": "对话 ID 无效"}
        return self._agent_bridge(["agent_chat_load", folder, conversation_id], timeout=30)

    def chat_postmeeting_offer(self, folder: str, conversation_id: str = "") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        cid = conversation_id if (conversation_id and
                                   self._agent_valid_session(conversation_id)) else ""
        return self._agent_bridge(["agent_chat_postmeeting_offer", folder, cid],
                                  timeout=120)

    def _finalize_chat_action(self, folder: str, conversation_id: str, action_id: str,
                              receipt: dict) -> dict:
        """Private: receipts come only from trusted desktop handlers, not JS/model input."""
        payload = self._agent_payload_file(receipt)
        try:
            result = self._agent_bridge(["chat_action_finalize", folder, conversation_id,
                                         action_id, str(payload)], timeout=120)
        finally:
            try:
                payload.unlink()
            except OSError:
                pass
        if not result.get("status"):
            result.update(ok=False, status="unverified", journal_pending=True,
                          error="执行结果尚未持久确认，请查询原操作，勿直接重试")
        result.update(folder=folder, project=folder, conversation_id=conversation_id,
                      action_id=action_id)
        return result

    def chat_action(self, folder: str, conversation_id: str, action_id: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(conversation_id) or not self._agent_valid_session(action_id):
            return {"ok": False, "error": "对话或操作 ID 无效"}
        with self._action_lock:
            result = self._agent_bridge(["agent_chat_action", folder, conversation_id, action_id],
                                        timeout=180)
            if result.get("ok") is not True or result.get("ui_only") is not True:
                return result
            kind = result.get("action")
            try:
                if kind == "start_meeting":
                    with self._meeting_lock:
                        receipt = self._start_meeting(folder)
                        proc = self._proc
                        if receipt.get("ok") is True:
                            alive = proc is not None and proc.poll() is None
                            receipt = {**receipt, "ok": alive, "confirmation": "process_started",
                                       "error": "" if alive else "会议进程已退出"}
                elif kind == "stop_meeting":
                    with self._meeting_lock:
                        proc = self._proc
                        if Path(self._current_project or ".").resolve() != Path(folder).resolve():
                            receipt = {"ok": False, "error": "运行中的会议不属于此项目"}
                        else:
                            receipt = self._stop_meeting()
                    if receipt.get("ok") is True and proc is not None:
                        try:
                            code = proc.wait(timeout=40)
                            receipt = {"ok": code == 0, "pid": proc.pid, "exit_code": code,
                                       "error": "" if code == 0 else "会议非正常退出，请核对归档"}
                        except subprocess.TimeoutExpired:
                            # Continue observing the same process, never the replacement global.
                            def finish_stop() -> None:
                                code = proc.wait()
                                self._finalize_chat_action(folder, conversation_id, action_id,
                                    {"ok": code == 0, "pid": proc.pid, "exit_code": code,
                                     "error": "" if code == 0 else "会议非正常退出，请核对归档"})
                            threading.Thread(target=finish_stop, daemon=True).start()
                            return {**result, "status": "running", "pending": True,
                                    "error": "会议尚未确认退出，操作保持运行中"}
                elif kind == "start_project_reading":
                    with self._und_lock:
                        receipt = self._understand_start(folder, 0,
                            action_context=(folder, conversation_id, action_id))
                    if receipt.get("ok") is True:
                        return {**result, "status": "running", "pending": True}
                else:
                    receipt = {"ok": False, "error": "不支持的桌面操作"}
            except Exception:
                receipt = {"ok": False, "error": "桌面操作未能完成"}
            return self._finalize_chat_action(folder, conversation_id, action_id, receipt)

    def chat_action_status(self, folder: str, conversation_id: str, action_id: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(conversation_id) or not self._agent_valid_session(action_id):
            return {"ok": False, "error": "对话或操作 ID 无效"}
        return self._agent_bridge(["chat_action_status", folder, conversation_id, action_id], timeout=30)

    def chat_cancel(self, folder: str = "", conversation_id: str = "", run_id: str = "") -> dict:
        with self._chat_lock:
            identity = self._chat_identity
            if not identity or not all((folder, conversation_id, run_id)):
                return {"ok": False, "error": "请指定要取消的项目、对话和运行"}
            if (Path(folder).resolve() != Path(identity["folder"]).resolve()
                    or conversation_id != identity["conversation_id"]
                    or run_id != identity["run_id"]):
                return {"ok": False, "error_code": "run_mismatch", "error": "运行身份不匹配"}
            process = self._chat_proc
            if process is None or process.poll() is not None:
                return {"ok": False, "error": "该运行已经结束", **identity}
            try:
                process.kill()
                self._chat_cancelled = True
                return {"ok": True, "status": "cancel_requested", **identity}
            except OSError:
                return {"ok": False, "error": "停止运行失败", **identity}

    def chat_send(self, folder: str, conversation_id: str, message: str,
                  mode: str = "agent", run_id: str = "") -> dict:
        try:
            run_id = str(UUID(run_id)) if run_id else str(uuid4())
        except (ValueError, TypeError, AttributeError):
            return {"ok": False, "error": "运行 ID 必须是 UUID"}
        identity = {"folder": folder, "project": folder, "conversation_id": conversation_id,
                    "thread_id": conversation_id, "run_id": run_id}
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效", **identity}
        if not self._agent_valid_session(conversation_id):
            return {"ok": False, "error": "对话 ID 无效", **identity}
        if not isinstance(message, str) or not message.strip():
            return {"ok": False, "error": "请输入消息", **identity}
        mode = "chat" if str(mode).lower() == "chat" else "agent"
        return self._agent_bridge_stream(["agent_chat_send", folder, conversation_id,
                                          message.strip()[:2000], mode, run_id],
                                         use_llm=True, timeout=300, identity=identity)

    def architecture(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["architecture", str(Path(folder).resolve())], timeout=60)

    def architecture_generate(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["architecture_generate", str(Path(folder).resolve())],
                                  use_llm=True, timeout=900)

    def architecture_save(self, folder: str, payload: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(payload, str) or len(payload) > 500_000:
            return {"ok": False, "error": "架构保存内容过大或无效"}
        return self._agent_bridge(["architecture_save", str(Path(folder).resolve()), payload],
                                  timeout=60)

    def kg_status(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["kg_status", folder], timeout=60)

    def kg_build(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["kg_build", folder], timeout=300)

    def kg_extract_project(self, folder: str, max_chunks: int = 8) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["kg_extract_project", folder,
                                   str(max(1, min(20, int(max_chunks or 8))))],
                                  use_llm=True, timeout=600)

    def kg_list(self, folder: str, limit: int = 800) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["kg_list", folder,
                                   str(max(1, min(2000, int(limit or 800))))], timeout=60)

    def kg_add_node(self, folder: str, kind: str, name: str, description: str = "") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not str(name or "").strip():
            return {"ok": False, "error": "实体名不能为空"}
        return self._agent_bridge(["kg_add_node", folder, str(kind)[:40],
                                   str(name)[:160], str(description)[:1000]], timeout=60)

    def kg_add_edge(self, folder: str, src: str, dst: str, relation: str,
                    valid_from: str = "") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not src or not dst or not relation:
            return {"ok": False, "error": "关系端点或类型不能为空"}
        return self._agent_bridge(["kg_add_edge", folder, str(src)[:200], str(dst)[:200],
                                   str(relation)[:40], str(valid_from)[:40]], timeout=60)

    def kg_delete_node(self, folder: str, identifier: str) -> dict:
        if not folder or not Path(folder).is_dir() or not identifier:
            return {"ok": False, "error": "实体无效"}
        return self._agent_bridge(["kg_delete_node", folder, str(identifier)[:200]], timeout=60)

    def kg_delete_edge(self, folder: str, identifier: str) -> dict:
        if not folder or not Path(folder).is_dir() or not identifier:
            return {"ok": False, "error": "关系无效"}
        return self._agent_bridge(["kg_delete_edge", folder, str(identifier)[:200]], timeout=60)

    def kg_ingest(self, folder: str, session: str = "", all_sessions: bool = False) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        sid = session if self._agent_valid_session(session) else ""
        return self._agent_bridge(["kg_ingest", folder, sid,
                                   "true" if all_sessions else "false",
                                   "false", "20"], use_llm=True, timeout=900)

    def kg_neighbors(self, folder: str, query: str, depth: int = 1, as_of: str = "") -> dict:
        if not folder or not Path(folder).is_dir() or not str(query or "").strip():
            return {"ok": False, "error": "项目或实体无效"}
        return self._agent_bridge(["kg_neighbors", folder, str(query)[:300],
                                   str(max(1, min(3, int(depth)))),
                                   str(as_of or "")[:40]], timeout=60)

    def kg_timeline(self, folder: str, query: str) -> dict:
        if not folder or not Path(folder).is_dir() or not str(query or "").strip():
            return {"ok": False, "error": "项目或实体无效"}
        return self._agent_bridge(["kg_timeline", folder, str(query)[:300]], timeout=60)

    def kg_path(self, folder: str, src: str, dst: str, max_depth: int = 3) -> dict:
        if not folder or not Path(folder).is_dir() or not src or not dst:
            return {"ok": False, "error": "项目或实体无效"}
        return self._agent_bridge(["kg_path", folder, str(src)[:300], str(dst)[:300],
                                   str(max(1, min(4, int(max_depth))))], timeout=60)

    def kg_merge(self, folder: str, source: str, target: str) -> dict:
        if not folder or not Path(folder).is_dir() or not source or not target:
            return {"ok": False, "error": "项目或实体无效"}
        return self._agent_bridge(["kg_merge", folder, str(source)[:300],
                                   str(target)[:300]], timeout=60)

    def kg_review_edge(self, folder: str, edge_id: str, status: str) -> dict:
        if status not in {"asserted", "pending", "retracted", "superseded"}:
            return {"ok": False, "error": "状态无效"}
        return self._agent_bridge(["kg_review_edge", folder, edge_id, status], timeout=60)

    def kg_ask(self, folder: str, question: str) -> dict:
        if not folder or not Path(folder).is_dir() or not str(question or "").strip():
            return {"ok": False, "error": "项目或问题无效"}
        return self._agent_bridge(["kg_ask", folder, str(question)[:1000]],
                                  use_llm=True, timeout=300)

    def kg_search(self, folder: str, query: str) -> dict:
        if not folder or not Path(folder).is_dir() or not str(query or "").strip():
            return {"ok": False, "error": "项目或查询无效"}
        return self._agent_bridge(["kg_search", folder, str(query)[:500]], timeout=120)

    def evidence_packs(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效", "packs": []}
        root = Path(folder) / "meetingvar" / "evidence_packs"
        if not root.is_dir():
            return {"ok": True, "packs": []}
        packs = []
        for directory in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not directory.is_dir():
                continue
            manifest_path = directory / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
            archive = directory.with_suffix(".zip")
            packs.append({
                "name": directory.name,
                "directory": str(directory),
                "zip": str(archive) if archive.is_file() else "",
                "created_at": manifest.get("generated_at", ""),
                "session_id": manifest.get("session_id", ""),
                "clip_id": manifest.get("clip_id", ""),
                "window": manifest.get("window", {}),
                "files": len(manifest.get("files", []) or []),
                "manifest": manifest,
            })
        return {"ok": True, "packs": packs[:100]}

    def evidence_verify(self, folder: str, name: str) -> dict:
        """Re-hash every file recorded in manifest.json; never trust stored hashes."""
        if not folder or not Path(folder).is_dir() or not name:
            return {"ok": False, "error": "项目或证据包无效"}
        root = (Path(folder) / "meetingvar" / "evidence_packs").resolve()
        target = (root / name).resolve()
        if root not in target.parents or not target.is_dir():
            return {"ok": False, "error": "证据包路径无效"}
        try:
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "error": "manifest 不可读"}
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(files, list) or not files:
            return {"ok": False, "error": "manifest 没有可校验文件",
                    "checked": [], "failed": [], "files": 0}
        checked, failed = [], []
        for item in files:
            if not isinstance(item, dict):
                failed.append({"path": "", "error": "文件记录格式无效"})
                continue
            rel = str(item.get("path") or "")
            expected = str(item.get("sha256") or "").lower()
            if not rel or not re.fullmatch(r"[0-9a-f]{64}", expected):
                failed.append({"path": rel, "actual": "", "expected": expected})
                continue
            path = (target / rel).resolve()
            if target not in path.parents or not path.is_file():
                failed.append({"path": rel, "actual": "", "expected": expected})
                continue
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                actual = digest.hexdigest()
                row = {"path": rel, "actual": actual, "expected": expected,
                       "ok": bool(expected) and actual == expected}
                checked.append(row)
                if not row["ok"]:
                    failed.append(row)
            except OSError:
                failed.append({"path": rel, "actual": "", "expected": expected})
        return {"ok": not failed, "checked": checked, "failed": failed,
                "files": len(checked), "zip": str(target.with_suffix(".zip"))
                if target.with_suffix(".zip").is_file() else ""}

    def commitment_ledger(self, folder: str, query: str = "") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["commitment_ledger", folder, str(query or "")], timeout=120)

    def find_moment(self, folder: str, query: str, session_id: str = "", limit: int = 5) -> dict:
        if not folder or not Path(folder).is_dir() or not str(query or "").strip():
            return {"ok": False, "error": "项目或查询无效"}
        if session_id and not self._agent_valid_session(session_id):
            return {"ok": False, "error": "会话 ID 无效"}
        return self._agent_bridge(["find_moment", folder, str(query), session_id,
                                   str(max(1, min(20, int(limit))))], timeout=120)

    def workspace_start(self, folder: str, force: bool = False) -> dict:
        if not folder or not Path(folder).is_dir():
            return {'ok': False, 'error': '项目文件夹无效'}
        return self._agent_bridge(['workspace_start', str(Path(folder).resolve()), '1' if force else '0'], timeout=60)

    def workspace_status(self, folder: str, job_id: str = '') -> dict:
        if not folder or not Path(folder).is_dir():
            return {'ok': False, 'error': '项目文件夹无效'}
        return self._agent_bridge(['workspace_status', str(Path(folder).resolve()), job_id], timeout=30)

    def workspace_subscribe(self, folder: str, job_id: str = '') -> dict:
        # Stateless polling subscription: reopening the UI attaches to persisted identity.
        return self.workspace_status(folder, job_id)

    def workspace_sync(self, folder: str) -> dict:
        """Agent-maintained workspace memory; no model call."""
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["workspace_sync", folder], timeout=180)

    def rag_index(self, folder: str, include_meetings: bool = True,
                  force: bool = False) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["rag_index", folder,
                                   "true" if include_meetings else "false",
                                   "true" if force else "false"], timeout=1800)

    def rag_status(self, folder: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        return self._agent_bridge(["rag_status", folder], timeout=60)

    def rag_ask(self, folder: str, question: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(question, str) or not question.strip():
            return {"ok": False, "error": "请输入问题"}
        return self._agent_bridge(["rag_ask", folder, question.strip()[:1000]],
                                  use_llm=True, timeout=300)

    def agent_memory(self, folder: str, query: str = "", status: str = "active") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        args = ["agent_memory", folder,
                query.strip()[:200] if isinstance(query, str) else "",
                status if status in {"active", "all", "pending-conflict", "superseded",
                                     "expired"} else "active"]
        return self._agent_bridge(args, timeout=60)

    def agent_memory_update(self, folder: str, name: str, payload: dict) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(name, str) or not name.strip() or not isinstance(payload, dict):
            return {"ok": False, "error": "记忆参数无效"}
        temp = self._agent_payload_file(payload)
        try:
            return self._agent_bridge(["agent_memory_update", folder,
                                       name.strip()[:300], str(temp)], timeout=60)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def agent_memory_delete(self, folder: str, name: str, hard: bool = False) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "记忆名称无效"}
        return self._agent_bridge(["agent_memory_delete", folder, name.strip()[:300],
                                   "true" if hard else "false"], timeout=60)

    def agent_memory_pin(self, folder: str, name: str, pinned: bool = True) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "记忆名称无效"}
        return self._agent_bridge(["agent_memory_pin", folder, name.strip()[:300],
                                   "true" if pinned else "false"], timeout=60)

    def agent_memory_resolve(self, folder: str, name: str, resolution: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if resolution not in {"keep_new", "keep_old", "merge"}:
            return {"ok": False, "error": "冲突处理方式无效"}
        return self._agent_bridge(["agent_memory_resolve", folder, name.strip()[:300],
                                   resolution], timeout=60)


def _install_crash_handler(backend: "Backend") -> None:
    def handle(exc_type, exc, tb) -> None:
        try:
            log(f"[crash] {getattr(exc_type, '__name__', 'Exception')}")
            if backend._cfg.get("crash_reports_enabled", True):
                from meetingvar.commercial.telemetry import upload_crash, write_crash

                path = write_crash(exc_type, exc, tb, app_version=APP_VERSION,
                                   logs_dir=CONFIG_DIR,
                                   context={"log": backend._last_log[:1000]})
                if path and backend._cfg.get("telemetry_enabled") and backend._cfg.get("telemetry_endpoint"):
                    upload_crash(path, backend._cfg["telemetry_endpoint"])
        except Exception:  # noqa: BLE001
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = handle
    if hasattr(threading, "excepthook"):
        def thread_handle(args) -> None:
            handle(args.exc_type, args.exc_value, args.exc_traceback)
        threading.excepthook = thread_handle


def main() -> None:
    # Keep the 1360px window in CSS pixels on 125%/150% Windows scaling;
    # otherwise WebView2 reports a ~900px viewport and the inspector is clipped.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass

        # 会议录制时窗口常被其他应用遮住。WebView2 默认会节流后台窗口的定时器/
        # 渲染，导致 pywebview 注入延迟、前端 6s 内拿不到桥（页面一直用 mock，
        # 录音按钮像“点不动”）。显式关闭后台节流，轮询和字幕才能在后台继续。
        flags = ("--disable-background-timer-throttling "
                 "--disable-backgrounding-occluded-windows "
                 "--disable-renderer-backgrounding "
                 "--disable-features=CalculateNativeWinOcclusion")
        existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
        if flags not in existing:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (existing + " " + flags).strip()

    import webview

    if not _webview2_available():
        message = ("meetingVAR 需要 Microsoft Edge WebView2 Runtime。\n\n"
                   "请安装 Evergreen WebView2 Runtime 后重新启动：\n"
                   "https://developer.microsoft.com/microsoft-edge/webview2/")
        log("[ui] 缺少 WebView2 Runtime，无法创建窗口")
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, message, "meetingVAR", 0x10)
            except Exception:
                pass
        return

    selftest = "--selftest" in sys.argv
    smoke = "--smoke-engine" in sys.argv
    log(f"[ui] {APP_NAME} {APP_VERSION} 启动（selftest={selftest} smoke={smoke}）")
    backend = Backend()
    _install_crash_handler(backend)

    if smoke:
        _smoke_engine(backend)
        return

    web_server = None
    page_url = str(WEB_DIR / "index.html")
    if (WEB_NEXT / "index.html").is_file():
        web_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(_NextWebHandler, directory=str(WEB_NEXT)),
        )
        threading.Thread(target=web_server.serve_forever, daemon=True).start()
        page_url = f"http://127.0.0.1:{web_server.server_port}/index.html"
        log(f"[ui] Next UI {page_url}")

    window = webview.create_window(
        f"{APP_NAME} {APP_VERSION}",
        url=page_url,
        js_api=backend,
        width=1360, height=860, x=0, y=0, min_size=(1100, 700),
    )
    backend._window = window
    window.events.closing += backend._allow_close
    if not selftest:
        backend._start_tray()
    if selftest:
        # 计时线程放 main()（早于 webview.start）：实测放在 _on_start 回调里的线程
        # 不会被执行到；放这里与官方 auto-close 示例一致。
        _selftest_close(window, backend, 14.0)

    def _on_start() -> None:
        backend._ensure_server()
        log("[ui] 窗口就绪")

    try:
        webview.start(_on_start, debug=selftest)  # 自检开 debug：JS 控制台错误可见
    finally:
        if web_server is not None:
            web_server.shutdown()
            web_server.server_close()
    backend.shutdown()  # 关窗即收尾：停会议 + 停本应用起的 LLM 服务（防孤儿进程）
    if selftest:
        # 正常路径：start 返回 = 窗口已关闭
        print("SELFTEST-OK", flush=True)


def _selftest_close(window: webview.Window, backend: "Backend", delay: float = 10.0) -> None:
    """自检专用终止器：pywebview 的 destroy() 从后台线程调用会在 WinForms 上死锁
    （实测），因此自检到点直接硬退出——自检的目标是"开窗+后端引导+前端JS桥"，
    正常关闭路径由用户点 X（原生行为）负责。"""
    def _close():
        time.sleep(delay)
        ok = backend._page_ready
        log(f"[ui] selftest 到点：窗口+后端+前端JS桥 验证={'通过' if ok else '失败'}，硬退出")
        print("SELFTEST-OK" if ok else "SELFTEST-FAIL: 前端未就绪（JS 桥未回报）", flush=True)
        os._exit(0 if ok else 4)
    threading.Thread(target=_close, daemon=True).start()


def _smoke_engine(backend: Backend) -> None:
    """无窗口演练：起会→等状态→手动 VAR→停止，打印事件摘要（验证子进程桥全链路）。"""
    tmp_project = Path(os.environ.get("TEMP", ".")) / "mv_ui_smoke_project"
    tmp_project.mkdir(exist_ok=True)
    (tmp_project / "说明.md").write_text("# 冒烟测试项目\n这是一个用于 UI 冒烟测试的临时项目。\n", encoding="utf-8")

    # 冒烟不得污染用户配置里的"当前项目"与"项目列表"（临时目录会被清掉，曾出现在界面/会议库）
    saved_cfg = {k: v for k, v in backend._cfg.items() if k in ("last_project", "projects")}
    r = backend.start_meeting(str(tmp_project), mode="go")
    print("start:", r, flush=True)
    if not r.get("ok"):
        sys.exit(2)

    deadline = time.time() + 240
    seen_rec = False
    while time.time() < deadline:
        ev = backend.poll_events(0)
        for e in ev["events"]:
            if e["type"] in ("ask", "hit", "exit"):
                print("ev:", {k: v for k, v in e.items() if k in ("type", "text", "spk", "code")}, flush=True)
            if "会议监听开始" in e.get("text", ""):
                seen_rec = True
        if seen_rec:
            print("监听已开始（检测到 [rec] 行）", flush=True)
            break
        if any(e["type"] == "exit" for e in ev["events"]):
            print("engine exited prematurely", flush=True)
            sys.exit(3)
        time.sleep(2)

    print("监听已开始，等待 15s 后发手动 VAR…", flush=True)
    time.sleep(15)
    print("manual_var:", backend.manual_var(), flush=True)
    time.sleep(10)
    print("stop:", backend.stop_meeting(), flush=True)

    deadline = time.time() + 150
    nxt = 0
    while time.time() < deadline:
        ev = backend.poll_events(nxt)
        nxt = ev["next"]
        for e in ev["events"]:
            if e["type"] in ("exit", "hit", "status"):
                print("ev:", {k: v for k, v in e.items() if k in ("type", "text", "code")}, flush=True)
        if any(e["type"] == "exit" for e in ev["events"]):
            break
        time.sleep(2)
    # 引擎原始日志尾巴（含 [done]/归档/异常），便于核对退出码 0/1 的真实原因
    all_ev = backend.poll_events(0)["events"]
    tail = [e for e in all_ev if e["type"] == "log"][-25:]
    print("---- engine raw tail ----", flush=True)
    for e in tail:
        print("  |", e.get("text", ""), flush=True)
    # 恢复用户原本的"当前项目"与"项目列表"（冒烟只借用配置，不留痕——
    # 曾漏恢复 projects，临时项目出现在会议库里）
    for k, v in saved_cfg.items():
        backend._cfg[k] = v
    save_config(backend._cfg)
    # 清掉本场冒烟在产物池留下的会话目录（否则会以"空会话"出现在会议库）
    sids = [e.get("text", "").split("[done] session=", 1)[1].split()[0]
            for e in all_ev if "[done] session=" in e.get("text", "")]
    for sid in sids[-1:]:
        for sub in ("reports", "sessions"):
            shutil.rmtree(Path(backend._cfg.get("repo_dir") or ".") / "data" / sub / sid,
                          ignore_errors=True)
    print("SMOKE-DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
