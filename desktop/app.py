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
import signal
import subprocess
import sys
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 注意：webview 延迟导入（仅 pick_folder/main 需要）——让本模块在无 pywebview 的
# 测试环境（CI model-free）里可 import，后端逻辑可单测。

APP_NAME = "meetingVAR"
APP_VERSION = "0.1.0"

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

ENGINE_PY_CANDIDATES = [
    r"D:\miniconda3\envs\meetingvar\python.exe",
]
REPO_CANDIDATES = [
    r"C:\Users\Lenovo\projects\meetingVAR",
]

CONFIG_DEFAULTS = {
    "engine_python": "",
    "repo_dir": "",
    "device": "cuda",
    "last_project": "",
    "projects": [],          # 已知项目（会议归档分组用；start_meeting 时维护）
    "llm_gguf": "",          # 本地 4B GGUF 路径（LLM 服务启动用；自动探测）
    "use_llm": False,        # 开会时是否叠加 LLM 语义层（需服务在线）
    "audio_source": "both",  # 采集源：both=系统声音+麦克风 / loopback / mic
    "asr_engine": "local",   # 识别引擎：local=本地 FunASR（含说话人）/ xfyun=讯飞语音听写
    "xfyun_dir": "",         # 讯飞控制台下载的文件夹（自动认领 .env：听写 / 录音文件转写）
    "llm_provider": "local", # 模型服务：local=内置 llama.cpp 服务 / custom=自定义 OpenAI 兼容 API
    "llm_base_url": "",      # custom: 形如 https://api.xxx.com/v1
    "llm_model": "",         # custom: 模型名
    "llm_api_key": "",       # custom: API Key（仅存本机配置）
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


def _first_existing(paths: list[str]) -> str:
    for p in paths:
        if p and Path(p).exists():
            return p
    return ""


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Backend:
    def __init__(self) -> None:
        self._cfg = load_config()
        self._window: webview.Window | None = None
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._events: list[dict] = []
        self._events_lock = threading.Lock()
        self._event_seq = 0
        self._meeting_started_at: float | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_port = 0
        self._page_ready = False
        self._current_project = ""
        self._last_log = ""
        self._llm_proc: subprocess.Popen | None = None
        # 文件转写任务（后台线程 + 进度轮询）
        self._tf: dict = {"running": False, "done": False, "ok": False,
                          "file": "", "progress": "", "out": "", "error": ""}
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
            try:
                hits = sorted(Path("D:/env/models/models").glob("Qwen--Qwen3-*GGUF*/**/*.gguf"))
                self._cfg["llm_gguf"] = str(hits[0]) if hits else ""
            except OSError:
                self._cfg["llm_gguf"] = ""

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
        return env

    _VAR_RE = re.compile(r">>> VAR!")
    _LINE_RE = re.compile(r"^\s*\[(\d{1,2}:\d{2}:\d{2})\]\s*(\S+):\s*(.+)$")
    _ASK_RE = re.compile(r"\[spk\] 检测到新说话人\s*(spk\w+?)\s*[（(]")
    _SAMPLE_RE = re.compile(r"刚才说：「([^」]*)」")
    _DONE_RE = re.compile(r"\[done\] session=(\S+)")

    def _handle_line(self, raw: str) -> None:
        line = raw.rstrip("\r\n")
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
            self._push("exit", code=code, last_log=self._last_log[-200:])
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
        """只读预览：用引擎 Python 跑 engine_bridge（不写任何文件）。"""
        py = self._cfg.get("engine_python") or ""
        repo = self._cfg.get("repo_dir") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未找到引擎 Python（设置页可手动指定）"}
        try:
            r = subprocess.run(
                [py, str(BRIDGE_PY), "preview", folder],
                cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180, env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "预览超时（>180s）"}
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return {"ok": False, "error": f"引擎无输出（rc={r.returncode}）: {(r.stderr or '')[-200:]}"}
        try:
            data = json.loads(out[-1])
            data["ok"] = True
            return data
        except ValueError:
            return {"ok": False, "error": f"引擎输出无法解析: {out[-1][:200]}"}

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
        cmd += ["--device", self._cfg.get("device", "cuda")]
        cmd += ["--source", self._cfg.get("audio_source", "both")]
        if self._cfg.get("asr_engine") == "xfyun":
            xdir = (self._cfg.get("xfyun_dir") or "").strip()
            if not xdir or not Path(xdir).exists():
                return {"ok": False, "error": "已选讯飞识别，但未配置讯飞文件夹（设置页 → 识别引擎）"}
            cmd += ["--asr", "xfyun", "--xfyun-cred", xdir]
        env = self._engine_env()
        provider = self._cfg.get("llm_provider") or "local"
        if provider == "custom":
            # 自定义 OpenAI 兼容端点：环境变量直接可被引擎的语义层/纪要层使用
            base = (self._cfg.get("llm_base_url") or "").strip()
            if base:
                env["MEETINGVAR_LLM_BASE_URL"] = base.rstrip("/")
                env["MEETINGVAR_LLM_API_KEY"] = (self._cfg.get("llm_api_key") or "").strip() or "sk-none"
                env["MEETINGVAR_LLM_MODEL"] = (self._cfg.get("llm_model") or "").strip() or "custom"
                if self._cfg.get("use_llm"):
                    cmd.append("--llm")
            elif self._cfg.get("use_llm"):
                log("[ui] 已勾选叠加 LLM 但自定义端点未填 base_url——本次未叠加")
        else:
            if self._cfg.get("use_llm") and self.llm_status().get("up"):
                cmd.append("--llm")  # 服务在线才叠加，避免白等
            # 把本地 LLM 服务指给引擎（否则 go --llm / 语义层永远"不可用"——真机实测坑）
            st = self.llm_status()
            env["MEETINGVAR_LLM_BASE_URL"] = f"http://127.0.0.1:{LLM_PORT}/v1"
            env.setdefault("MEETINGVAR_LLM_API_KEY", "local")
            env["MEETINGVAR_LLM_MODEL"] = st.get("model") or "local"
        flags = _SPAWN_FLAGS  # 无控制台黑窗（用户实测："点开始会议弹出脚本页"）
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
        return self._write_stdin("\n")  # 空行 = 引擎的手动 VAR

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
                "started_at": self._meeting_started_at}

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
                out.append({"sid": sid, "lines": lines, "clips": clips, "minutes": minutes, "mtime": mtime})
        out.sort(key=lambda x: -x["mtime"])
        return out[:100]

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
                    if hd_mp4.exists():
                        hd_url = self._media_url(str(hd_mp4))
                detail["clips"].append({
                    "clip_id": d.get("clip_id", "")[:8],
                    "layer": (d.get("trigger") or {}).get("layer", ""),
                    "evidence": (d.get("trigger") or {}).get("evidence", ""),
                    "at": (d.get("audio") or {}).get("end_ts", 0),
                    "lines": len(d.get("transcript", [])),
                    "audio_url": self._media_url((d.get("audio") or {}).get("path")),
                    "hd_url": hd_url,
                })
        return detail

    # ---- 本地 LLM 服务管理（llama.cpp，127.0.0.1:LLM_PORT）----
    def llm_status(self) -> dict:
        import urllib.request

        managed = self._llm_proc is not None and self._llm_proc.poll() is None
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机服务不走代理
            with op.open(f"http://127.0.0.1:{LLM_PORT}/v1/models", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            mid = (data.get("data") or [{}])[0].get("id", "")
            return {"up": True, "model": Path(str(mid)).name, "managed": managed,
                    "gguf": self._cfg.get("llm_gguf", "")}
        except Exception:  # noqa: BLE001
            return {"up": False, "model": "", "managed": managed, "gguf": self._cfg.get("llm_gguf", "")}

    def llm_start(self) -> dict:
        st = self.llm_status()
        if st["up"]:
            return {"ok": True, "already": True, "model": st["model"]}
        py = self._cfg.get("engine_python") or ""
        gguf = self._cfg.get("llm_gguf") or ""
        if not py or not Path(py).exists():
            return {"ok": False, "error": "未配置引擎 Python"}
        if not gguf or not Path(gguf).exists():
            return {"ok": False, "error": "未找到 GGUF 模型（设置页可手动填路径）"}
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            logf = open(LLM_LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 — 进程存活期持有
        except OSError:
            logf = subprocess.DEVNULL
        try:
            self._llm_proc = subprocess.Popen(
                [py, "-m", "llama_cpp.server", "--model", gguf, "--n_ctx", "32768", "--port", str(LLM_PORT)],
                cwd=self._cfg.get("repo_dir") or None, stdout=logf, stderr=subprocess.STDOUT,
                env=self._engine_env(), creationflags=_SPAWN_FLAGS,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动失败: {exc}"}
        log(f"[ui] LLM 服务启动中 pid={self._llm_proc.pid}（模型 {Path(gguf).name}）")
        return {"ok": True, "starting": True}

    def llm_stop(self) -> dict:
        st = self.llm_status()
        if not st["up"] and not st["managed"]:
            return {"ok": False, "error": "服务未在运行"}
        if not st["managed"]:
            return {"ok": False, "error": "该服务不是本应用启动的（可能在别的终端里），未终止它"}
        try:
            self._llm_proc.terminate()
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

    def test_llm(self) -> dict:
        """按当前 provider 测连接：local=本机服务 /v1/models；custom=远程 OpenAI 兼容端点。"""
        provider = self._cfg.get("llm_provider") or "local"
        if provider == "local":
            st = self.llm_status()
            if st["up"]:
                return {"ok": True, "provider": "local", "model": st["model"], "note": "本地服务在线"}
            return {"ok": False, "provider": "local", "error": "本地服务未运行（可点「启动服务」）"}
        import urllib.error
        import urllib.request

        base = (self._cfg.get("llm_base_url") or "").strip().rstrip("/")
        if not base:
            return {"ok": False, "provider": "custom", "error": "未填写 Base URL"}
        url = base + "/models"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {(self._cfg.get('llm_api_key') or 'sk-none').strip()}"})
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            ids = [m.get("id", "") for m in (data.get("data") or []) if isinstance(m, dict)][:5]
            return {"ok": True, "provider": "custom", "models": ids, "note": f"连通，{len(ids)} 个模型可见"}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "provider": "custom", "error": f"HTTP {exc.code}（鉴权或路径问题）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": "custom", "error": f"{type(exc).__name__}: {str(exc)[:140]}"}

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


def main() -> None:
    import webview

    selftest = "--selftest" in sys.argv
    smoke = "--smoke-engine" in sys.argv
    log(f"[ui] {APP_NAME} {APP_VERSION} 启动（selftest={selftest} smoke={smoke}）")
    backend = Backend()

    if smoke:
        _smoke_engine(backend)
        return

    window = webview.create_window(
        f"{APP_NAME} {APP_VERSION}",
        url=str(WEB_DIR / "index.html"),
        js_api=backend,
        width=900, height=640, min_size=(760, 520),
    )
    backend._window = window
    if selftest:
        # 计时线程放 main()（早于 webview.start）：实测放在 _on_start 回调里的线程
        # 不会被执行到；放这里与官方 auto-close 示例一致。
        _selftest_close(window, backend, 10.0)

    def _on_start() -> None:
        backend._ensure_server()
        log("[ui] 窗口就绪")

    webview.start(_on_start, debug=selftest)  # 自检开 debug：JS 控制台错误可见
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

    # 冒烟不得污染用户配置里的"当前项目"（临时目录会被清掉，曾出现在界面上）
    saved_project = backend._cfg.get("last_project", "")
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
    # 恢复用户原本的"当前项目"（冒烟只借用配置，不留痕）
    backend._cfg["last_project"] = saved_project
    save_config(backend._cfg)
    print("SMOKE-DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
