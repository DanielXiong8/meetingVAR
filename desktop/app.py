"""meetingVAR 桌面壳（oCam 风格 v0.1）：pywebview 窗口 + CLI 子进程桥。

架构（调研报告 §4）：UI 只做壳——
- 开会 = 启动引擎子进程 `python -m meetingvar go/record …`（cwd=仓库根）；
- 事件 = 引擎 stdout 按行解析成结构化事件，前端轮询 poll_events()；
- 输入 = 手动 VAR 写一个空行、点名应答写名字——正好对接引擎现有的 input() 线程；
- 结束 = CTRL_BREAK_EVENT → 引擎走键盘中断的优雅收尾（尾块转写/零落盘纪律不变）。
UI 不 import 引擎核心、不碰保存链路。

命令行（供构建/自检）：
    python app.py              正常启动
    python app.py --selftest   开窗 8 秒自动关闭并打印 SELFTEST-OK（CI/打包冒烟）
    python app.py --smoke-engine  不开窗口，直接演练"起会→手动VAR→停止"全链路
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 注意：webview 延迟导入（仅 pick_folder/main 需要）——让本模块在无 pywebview 的
# 测试环境（CI model-free）里可 import，后端逻辑可单测。

APP_NAME = "meetingVAR"
APP_VERSION = "0.10.1"

# 冻结（PyInstaller onefile）时资源在 _MEIPASS；源码运行时在本目录
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = _BASE / "web"
BRIDGE_PY = _BASE / "engine_bridge.py"

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
    "llm_api_context_tokens": 32768,
    "llm_thinking": "",
    "use_llm": False,        # 旧版配置兼容，实时兴趣由 interest_mode 控制
    "interest_mode": "api",
    "interest_focus": "",
    "meeting_engine_version": "0.1.2",
    "audio_source": "both",
    "asr_engine": "xfyun_rtasr",
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


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(saved)
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
        self._proc: subprocess.Popen | None = None
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
        self._page_ready = False
        self._current_project = ""
        self._last_log = ""
        self._llm_proc: subprocess.Popen | None = None
        self._llm_launch: dict = {}
        self._llm_error = ""
        self._llm_log_offset = 0
        self._llm_start_lock = threading.Lock()
        self._llm_verified: set[str] = set()
        self._llm_verified_at: dict[str, float] = {}
        try:
            validations = json.loads((CONFIG_DIR / "llm_validation.json").read_text(encoding="utf-8"))
            self._llm_verified_at = {key: stamp for key, stamp in validations.items()
                                     if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                                     and isinstance(stamp, (int, float)) and 0 <= time.time() - stamp < 86400}
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

    # ---------------- 引擎子进程 ----------------
    def _engine_env(self) -> dict:
        env = os.environ.copy()
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
            context = self._cfg.get("llm_api_context_tokens", 32768)
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
                "MEETINGVAR_LLM_THINKING": (self._cfg.get("llm_thinking") or "") if provider == "custom" else ""}

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
            self._push("exit", code=code, last_log=self._last_log[-200:], summary=self._meeting_summary)
            self._capture_phase = "idle"
            self._meeting_started_at = None

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
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if py and Path(py).exists():
            try:
                r = subprocess.run(
                    [py, str(BRIDGE_PY), "preview", folder],
                    cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=180, env=self._engine_env(), creationflags=_SPAWN_FLAGS,
                )
                out = (r.stdout or "").strip().splitlines()
                if out:
                    data = json.loads(out[-1])
                    data["ok"] = True
                    return data
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        try:
            from meetingvar.memory.preview import scan_project_folder

            return scan_project_folder(folder)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"本地预览失败：{type(exc).__name__}"}

    def start_meeting(self, folder: str = "", mode: str = "go") -> dict:
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
        interest = self._cfg.get("interest_mode", "api")
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
        engine = self._cfg.get("asr_engine", "xfyun_rtasr")
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
        if interest == "api":
            try:
                env.update(self._llm_env(provider="custom", purpose="interest"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
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

    def stop_meeting(self) -> dict:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return {"ok": False, "error": "没有正在运行的会议"}
        # 优雅路径：stdin 发 ":stop"。实测 CTRL_BREAK 会被引擎进程内 MKL 的
        # Fortran 运行库劫持成 forrtl abort（绕过全部收尾），信号只作兜底。
        try:
            if proc.stdin:
                proc.stdin.write(":stop\n")
                proc.stdin.flush()
                log(f"[ui] 已发送优雅停止指令（stdin :stop） pid={proc.pid}")
                return {"ok": True, "mode": "stdin"}
        except OSError as exc:
            log(f"[ui] stdin 停止失败({exc})，回退信号")
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            log(f"[ui] 已发送停止信号 pid={proc.pid}")
            return {"ok": True, "mode": "signal"}
        except (OSError, ValueError):
            try:
                proc.terminate()
                return {"ok": True, "mode": "terminate"}
            except OSError as exc2:
                return {"ok": False, "error": str(exc2)}

    def manual_var(self) -> dict:
        return self._write_stdin(":var\n")

    def answer_prompt(self, text: str) -> dict:
        return self._write_stdin((text or "").strip() + "\n")  # 名字（空=跳过）

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

    def poll_events(self, since: int = 0) -> dict:
        with self._events_lock:
            evs = [e for e in self._events if e["i"] > since]
            nxt = self._event_seq
        return {"events": evs, "next": nxt, "running": self.is_running(),
                "started_at": self._meeting_started_at, "screen": self._screen_state,
                "phase": self._capture_phase}

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
                [py, str(BRIDGE_PY), "check_xfyun", xdir],
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
            process = subprocess.run([py, str(BRIDGE_PY), "test_llm"], cwd=self._cfg.get("repo_dir") or None,
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
        self._tf = {"running": True, "file": path, "progress": "启动…", "done": False,
                    "ok": False, "out": "", "error": ""}
        try:
            proc = subprocess.Popen(
                [py, str(BRIDGE_PY), "transcribe_file", path, xdir],
                cwd=self._cfg.get("repo_dir") or None, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except OSError as exc:
            self._tf.update({"running": False, "done": True, "error": f"启动失败: {exc}"})
            return {"ok": False, "error": str(exc)}
        threading.Thread(target=self._tf_reader, args=(proc,), daemon=True).start()
        log(f"[ui] 文件转写任务启动: {path}")
        return {"ok": True, "file": Path(path).name}

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
                [py, str(BRIDGE_PY), "understand_plan", folder],
                cwd=self._cfg.get("repo_dir") or None, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
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
                     "progress": "启动…", "result": {}, "error": "", "max_chunks": max_chunks}
        cmd = [py, str(BRIDGE_PY), "understand", folder] + ([str(max_chunks)] if max_chunks else [])
        try:
            env = self._engine_env()
            env.update(self._llm_env())  # 全库理解必须有模型：本地服务/自定义端点在此校验
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
        threading.Thread(target=self._und_reader, args=(proc,), daemon=True).start()
        log(f"[ui] 全库理解任务启动: {folder} max_chunks={max_chunks or '全量'}")
        return {"ok": True, "folder": folder}

    def _und_reader(self, proc: subprocess.Popen) -> None:
        final: str | None = None
        try:
            for line in proc.stdout or []:
                line = line.rstrip("\r\n")
                if line.startswith("[progress] "):
                    self._und["progress"] = line[len("[progress] "):]
                elif line.startswith("{"):
                    final = line
        except Exception as exc:  # noqa: BLE001
            self._und["error"] = f"读取输出失败: {exc}"
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
        if final:
            try:
                data = json.loads(final)
                partial = bool(data.get("failed_chunks")) and int(data.get("chunks_read") or 0) > 0
                self._und.update({"ok": bool(data.get("ok")), "result": data, "partial": partial,
                                  "error": data.get("error") or "", "error_code": data.get("error_code"),
                                  "warnings": data.get("warnings") or [],
                                  "progress": "完成" if data.get("ok") else
                                              ("部分完成" if partial else "中断")})
            except ValueError:
                self._und["error"] = "结果解析失败"
        elif not self._und.get("error"):
            rc = proc.returncode
            self._und["error"] = ("已取消（成功块缓存保留）" if self._und.get("cancelled")
                                  else f"引擎异常退出（代码{rc}），未返回诊断结果")
        self._und["running"] = False
        self._und["done"] = True
        self._und_proc = None
        log(f"[ui] 全库理解结束: ok={self._und['ok']} err={self._und['error'] or '-'}")
        result = self._und.get("result") or {}
        self._push("understand_done", ok=self._und["ok"], error=self._und["error"],
                   partial=bool(self._und.get("partial")), folder=self._und["folder"],
                   chunks_read=result.get("chunks_read", 0),
                   chunks_total=result.get("chunks_total", 0),
                   coverage_pct=result.get("coverage_pct", 0.0))

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
        """把勾选的确认词写入 <folder>/meetingvar/terms.txt（#!keep，下次开会自动采用）。"""
        py = self._cfg.get("engine_python") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        words = [str(t).strip() for t in (terms or []) if str(t).strip()]
        if not words:
            return {"ok": False, "error": "没有勾选任何词"}
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_DIR / "_terms_confirm.json"
            tmp.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run(
                [py, str(BRIDGE_PY), "write_terms", folder, str(tmp)],
                cwd=self._cfg.get("repo_dir") or None, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
                env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return {"ok": False, "error": f"引擎无输出（rc={r.returncode}）"}
        try:
            return json.loads(out[-1])
        except ValueError:
            return {"ok": False, "error": f"输出解析失败: {out[-1][:120]}"}

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
                if proc.stdin:
                    proc.stdin.write(":stop\n")
                    proc.stdin.flush()
                    log("[ui] 退出：已发送优雅停止（:stop）")
                    try:
                        proc.wait(timeout=30)  # 收尾含 digest+归档，给足时间
                    except subprocess.TimeoutExpired:
                        log("[ui] 退出：引擎 30s 未退出，终止")
                        proc.terminate()
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

    def _allow_close(self) -> bool:
        if not self.is_running():
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
        return export_project(folder, target)

    def delete_project_data(self, folder: str, categories: list, confirm: str) -> dict:
        from meetingvar.commercial.data import delete_categories

        if not isinstance(categories, list):
            return {"ok": False, "error": "删除类别无效"}
        data_dir = Path(self._cfg.get("repo_dir") or _APP_HOME) / "data"
        return delete_categories(folder, data_dir, categories, confirm)

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
            r = subprocess.run([py, str(BRIDGE_PY), "check"], cwd=repo,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, env=self._engine_env(),
                               creationflags=_SPAWN_FLAGS)
            out = (r.stdout or "").strip().splitlines()
            if out:
                data = json.loads(out[-1])
                if data.get("ok"):
                    data["bridge"] = str(BRIDGE_PY)
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
        if use_llm:
            try:
                env.update(self._llm_env(purpose="reading"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        try:
            process = subprocess.run(
                [py, str(BRIDGE_PY), *args], cwd=repo, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=_SPAWN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Agent 任务超时；可在 CLI 查看/重试"}
        except OSError:
            return {"ok": False, "error": "Agent 子进程启动失败"}
        lines = [line for line in (process.stdout or "").splitlines() if line.startswith("{")]
        if not lines:
            return {"ok": False, "error": "Agent 没有返回结构化结果"}
        try:
            data = json.loads(lines[-1])
        except ValueError:
            return {"ok": False, "error": "Agent 结果无法解析"}
        return data if isinstance(data, dict) else {"ok": False, "error": "Agent 结果格式无效"}

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
        try:
            from meetingvar.memory.preview import read_project_file

            return read_project_file(folder, relative)
        except ImportError:
            # Older/custom builds may not include the preview package; the
            # engine bridge remains a compatible fallback.
            return self._agent_bridge(["read_project_file", folder, relative], timeout=120)
        except Exception as exc:  # noqa: BLE001
            log(f"[file] 本地预览失败：{type(exc).__name__}")
            return self._agent_bridge(["read_project_file", folder, relative], timeout=120)

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

    def chat_action(self, folder: str, conversation_id: str, action_id: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(conversation_id) or not self._agent_valid_session(action_id):
            return {"ok": False, "error": "对话或操作 ID 无效"}
        return self._agent_bridge(["agent_chat_action", folder, conversation_id, action_id],
                                  timeout=180)

    def chat_send(self, folder: str, conversation_id: str, message: str) -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        if not self._agent_valid_session(conversation_id):
            return {"ok": False, "error": "对话 ID 无效"}
        if not isinstance(message, str) or not message.strip():
            return {"ok": False, "error": "请输入消息"}
        return self._agent_bridge(["agent_chat_send", folder, conversation_id,
                                   message.strip()[:2000]], use_llm=True, timeout=300)

    def agent_memory(self, folder: str, query: str = "") -> dict:
        if not folder or not Path(folder).is_dir():
            return {"ok": False, "error": "项目文件夹无效"}
        args = ["agent_memory", folder]
        if isinstance(query, str) and query.strip():
            args.append(query.strip()[:200])
        return self._agent_bridge(args, timeout=60)


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

    window = webview.create_window(
        f"{APP_NAME} {APP_VERSION}",
        url=str(WEB_DIR / "index.html"),
        js_api=backend,
        width=1050, height=680, x=0, y=0, min_size=(900, 600),
    )
    backend._window = window
    window.events.closing += backend._allow_close
    if selftest:
        # 计时线程放 main()（早于 webview.start）：实测放在 _on_start 回调里的线程
        # 不会被执行到；放这里与官方 auto-close 示例一致。
        _selftest_close(window, backend, 10.0)

    def _on_start() -> None:
        backend._ensure_server()
        log("[ui] 窗口就绪")

    webview.start(_on_start, debug=selftest)  # 自检开 debug：JS 控制台错误可见
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
