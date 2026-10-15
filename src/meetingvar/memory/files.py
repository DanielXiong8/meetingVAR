"""Pruned project scanning: installed runtimes are not project memory."""
from __future__ import annotations

import os
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", ".tools", ".runtimes", "node_modules",
             "__pycache__", "dist", "build", ".zcode", ".secrets"}


def project_files(root: Path, suffixes: set[str]):
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = sorted(name for name in children if name.lower() not in SKIP_DIRS
                             and not name.lower().startswith(".venv-")
                             and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() in suffixes and not path.is_symlink():
                yield path


_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def read_text_safely(path: Path, max_bytes: int = 2_000_000) -> str | None:
    """健壮文本读取：二进制/伪装文件返回 None，绝不抛异常。

    实测教训（2026-10-15 真机）：一个伪装成 .txt 的 zip 文件同时引爆两个 bug——
    ① load_memory_docs 严格 utf-8 读取直接崩溃（引擎退出码 1，界面只见"会议已结束"）；
    ② extract_terms 带 errors=ignore 硬读 → 二进制垃圾混进词表（CQ/Dc/Fk…）。
    规则：zip 魔数 → 仅 .docx 尝试解析正文；含 NUL → 判二进制；utf-8 严格失败回退 GBK。
    """
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError:
        return None
    if not data:
        return None
    if data[:4] in _ZIP_MAGICS:
        return _read_docx(path) if path.suffix.lower() == ".docx" else None
    if b"\x00" in data[:4096]:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _read_docx(path: Path) -> str | None:
    """docx → 纯文本（zipfile 取 word/document.xml 粗剥标签；零第三方依赖）。"""
    import re
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)
