"""本地 LLM 启动参数：只发现文件、校验配置和构造命令，不启动进程或读写配置。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_HOME = (Path(sys.executable).resolve().parent
             if getattr(sys, "frozen", False) else _REPO_ROOT)
_RUNTIME = _APP_HOME / "runtime"
_SERVER_ROOT = Path("D:/env/llama.cpp")
_SERVER_NAMES = ("llama-server.exe", "llama-server")
_MODEL_ALIAS = "meetingvar-local"
_MAX_VERSION_DIRS = 32
_GENERIC_ERROR = "本地模型服务启动失败，请检查后端配置。"


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError, RuntimeError):
        return False


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label}必须是有效文本。")
    return value.strip()


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    # bool 是 int 的子类，但不应成为上下文、端口或 GPU 层数。
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须是 {minimum} 至 {maximum} 之间的整数。")
    return value


def _file(value: object, label: str) -> str:
    text = _text(value, label)
    if not text:
        raise ValueError(f"未配置{label}。")
    try:
        path = Path(text).expanduser()
        if _is_file(path):
            return str(path.resolve())
    except (OSError, ValueError, RuntimeError):
        pass
    raise ValueError(f"{label}文件不存在或不可访问。")


def _server_in(directory: Path) -> str:
    for name in _SERVER_NAMES:
        path = directory / name
        if _is_file(path):
            try:
                return str(path.resolve())
            except (OSError, ValueError, RuntimeError):
                continue
    return ""


def _version_key(path: Path) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", path.name)), path.name.lower()


def discover_server(repo_dir: str | None = None) -> str:
    """按环境覆盖、PATH、项目工具目录、固定 D 盘目录顺序发现原生服务。

    工具目录只查根目录、bin、至多 32 个直接版本子目录及其 bin，绝不递归。
    无可用文件时返回空字符串；不运行候选程序，也不读取其内容。
    """
    override = os.environ.get("MEETINGVAR_LLAMA_SERVER", "")
    if override:
        try:
            return _file(override, "原生服务程序")
        except ValueError:
            pass
    for name in _SERVER_NAMES:
        try:
            found = shutil.which(name)
            if found:
                return _file(found, "原生服务程序")
        except (OSError, ValueError):
            continue

    repo_text = _text(repo_dir, "项目目录") if repo_dir is not None else ""
    try:
        repo = Path(repo_text).expanduser() if repo_text else _REPO_ROOT
    except (OSError, ValueError, RuntimeError):
        raise ValueError("项目目录无效。") from None
    roots = [repo / ".tools" / "llama.cpp"]
    try:
        if (_RUNTIME / "llama.cpp").is_dir():
            roots.append(_RUNTIME / "llama.cpp")
    except OSError:
        pass
    roots.append(_SERVER_ROOT)
    for root in roots:
        for directory in (root, root / "bin"):
            found = _server_in(directory)
            if found:
                return found
        try:
            versions = sorted((p for p in root.iterdir() if _is_dir(p)),
                              key=_version_key, reverse=True)[:_MAX_VERSION_DIRS]
        except (OSError, ValueError, RuntimeError):
            continue
        for version in versions:
            for directory in (version, version / "bin"):
                found = _server_in(directory)
                if found:
                    return found
    return ""


def build_launch(config: dict, port: int) -> dict:
    """返回 cmd/backend/context_tokens/summary/model_alias；无效配置抛 ValueError。

    auto 优先原生服务，发现失败才回退 Python。显式配置的原生路径无效时
    直接报错。Python 固定使用 CPU、256 批量、logits_all=false，并将上下文
    封顶 8192；评分矩阵因此只需 n_batch × n_vocab，不随上下文扩大。
    """
    if not isinstance(config, dict):
        raise ValueError("本地模型配置必须是字典。")
    backend = _text(config.get("llm_backend", "auto"), "后端")
    if backend not in {"auto", "native", "python"}:
        raise ValueError("后端必须是 auto、native 或 python。")
    context = _integer(config.get("llm_context_tokens", 16384), "上下文长度", 2048, 32768)
    layers = _integer(config.get("llm_gpu_layers", -1), "GPU 层数", -1, 999)
    port = _integer(port, "服务端口", 1, 65535)
    device = _text(config.get("llm_device", ""), "推理设备")
    if device and (len(device) > 160 or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:/-]*(?:,[A-Za-z0-9][A-Za-z0-9_.:/-]*)*", device)):
        raise ValueError("推理设备必须是设备标识或以逗号分隔的设备标识。")

    model_text = _text(config.get("llm_gguf", ""), "GGUF 模型")
    if Path(model_text).suffix.lower() != ".gguf":
        raise ValueError("模型文件必须使用 .gguf 后缀。")
    model = _file(model_text, "GGUF 模型")
    repo_text = _text(config.get("repo_dir", ""), "项目目录")
    if repo_text:
        try:
            repo = Path(repo_text).expanduser()
            if not _is_dir(repo):
                raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise ValueError("项目目录不存在或不可访问。") from None
    server_text = _text(config.get("llm_server_exe", ""), "原生服务程序")
    server = ""
    if backend != "python":
        server = _file(server_text, "原生服务程序") if server_text else discover_server(repo_text or None)
        if server:
            server = _file(server, "原生服务程序")
        elif backend == "native":
            raise ValueError("未找到原生 llama-server 程序，请配置有效的程序路径。")

    if server:
        cmd = [server, "--model", model, "--ctx-size", str(context),
               "--n-gpu-layers", "all" if layers == -1 else str(layers), "--parallel", "1",
               "--batch-size", "512", "--ubatch-size", "256", "--flash-attn", "on",
               "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
               "--host", "127.0.0.1", "--port", str(port), "--alias", _MODEL_ALIAS,
               "--jinja", "--no-webui", "--chat-template-kwargs", json.dumps({"enable_thinking": False})]
        if device:
            cmd += ["--device", device]
        requested_layers = "全部" if layers == -1 else str(layers)
        return {"cmd": cmd, "backend": "native", "context_tokens": context,
                "summary": f"原生 llama.cpp · 上下文 {context} · GPU 层请求：{requested_layers}",
                "model_alias": _MODEL_ALIAS}

    python = _file(config.get("engine_python", ""), "引擎 Python")
    context = min(context, 8192)
    cmd = [python, "-m", "llama_cpp.server", "--model", model,
           "--n_ctx", str(context), "--n_gpu_layers", "0", "--n_batch", "256",
           "--logits_all", "false", "--host", "127.0.0.1", "--port", str(port)]
    return {"cmd": cmd, "backend": "python", "context_tokens": context,
            "summary": f"Python CPU 回退 · 上下文 {context} · 批量 256",
            # llama-cpp-python 未设置 model_alias 时，/v1/models 使用模型路径。
            "model_alias": model}


def safe_startup_error(log_text: str) -> str:
    """把日志映射到固定错误分类；绝不把路径、请求或原始日志返回界面。"""
    if not isinstance(log_text, str):
        return _GENERIC_ERROR
    text = log_text.lower()
    if ("vk_error_out_of_device_memory" in text or "显存不足" in text
            or re.search(r"(?:cuda|vulkan|hip|vram|gpu).{0,120}"
                         r"(?:out of memory|out_of_memory|\boom\b|allocation failed|alloc failed|not enough memory)",
                         text, re.S)):
        return "本地模型服务启动失败：显存不足或显存分配失败。"
    if re.search(r"\boom\b", text) or any(pattern in text for pattern in (
            "arraymemoryerror", "memoryerror", "bad_alloc", "unable to allocate",
            "cannot allocate memory", "out of memory", "out_of_host_memory",
            "not enough memory", "insufficient memory", "内存不足")):
        return "本地模型服务启动失败：内存不足或内存分配失败。"
    if any(pattern in text for pattern in (
            "address already in use", "only one usage of each socket address",
            "winerror 10048", "errno 98", "端口已被占用")):
        return "本地模型服务启动失败：端口已被占用。"
    missing = r"(?:not found|does not exist|no such file|cannot open|failed to open|找不到|不存在)"
    if re.search(r"(?:gguf|model|模型).{0,200}" + missing + "|" + missing
                 + r".{0,200}(?:gguf|model|模型)", text, re.S):
        return "本地模型服务启动失败：模型文件缺失或不可读取。"
    if (any(pattern in text for pattern in (
            "dll load failed", "failed to load shared library", "cannot load library",
            "could not find module", "modulenotfounderror", "no module named",
            "undefined symbol", "not a valid win32 application", "winerror 193"))
            or re.search(r"(?:dll|shared library|backend).{0,160}(?:not found|failed|cannot|missing)"
                         r"|(?:failed|cannot|unable).{0,160}(?:dll|shared library|backend)", text, re.S)):
        return "本地模型服务启动失败：后端库缺失、不兼容或无法加载。"
    return _GENERIC_ERROR
