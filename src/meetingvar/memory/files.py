"""Pruned project scanning + robust text extraction for project understanding."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", ".tools", ".runtimes", "node_modules",
             "__pycache__", "dist", "build", ".zcode", ".secrets"}

# Text-like files the agent may read. PDF/Office are converted to text.
SUPPORTED_TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".rst", ".log",
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xml", ".html", ".htm", ".xhtml", ".svg",
    ".tsv", ".csv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm",
    ".java", ".kt", ".kts", ".go", ".rs", ".rb", ".php", ".cs", ".swift",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".sql",
    ".css", ".scss", ".less", ".r", ".lua", ".pl", ".tex",
})

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".tif"})
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"})
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"})
OFFICE_SUFFIXES = frozenset({".docx", ".doc", ".pptx", ".ppt"})
QUICK_TEXT_SUFFIXES = SUPPORTED_TEXT_SUFFIXES - {".pdf", ".doc", ".ppt"}


def is_text_suffix(suffix: str) -> bool:
    return str(suffix).lower() in SUPPORTED_TEXT_SUFFIXES


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


def _cache_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "meetingvar_extract_cache"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(tempfile.gettempdir())
    return path


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        raw = f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{path.suffix.lower()}"
    except OSError:
        raw = f"{path}|missing"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def read_text_safely(path: Path, max_bytes: int = 8_000_000) -> str | None:
    """Extract readable text; never raise for binary/corrupt/unsupported files.

    Plain text is decoded with BOM/UTF-8/GBK fallbacks. DOCX/PPTX are parsed from
    their OOXML zip members. PDF uses PyMuPDF when available, then pdftotext.
    Legacy PPT/DOC use LibreOffice headless conversion with a temp cache.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES or suffix in AUDIO_SUFFIXES:
            return None
        if suffix == ".docx":
            return _read_docx(path)
        if suffix == ".pptx":
            return _read_pptx(path)
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix in {".ppt", ".doc"}:
            return _read_libreoffice(path)
        data = path.read_bytes()[:max_bytes]
    except OSError:
        return None
    if not data:
        return None
    if data[:4] in _ZIP_MAGICS:
        return None
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            body = data[2:]
            if len(body) % 2:
                body = body[:-1]
            probe = body[:4096]
            probe = probe[: len(probe) - len(probe) % 2]
            try:
                probe.decode(enc)
            except UnicodeDecodeError:
                return None
            return body.decode(enc, "replace")
    if b"\x00" in data[:4096]:
        return None
    for enc in ("utf-8-sig", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _read_docx(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    xml = re.sub(r"</w:p>", "\n", xml)
    import html
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return text.strip() or None


def _read_pptx(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            slides = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name)),
                key=lambda name: int(re.search(r"([0-9]+)\.xml$", name).group(1)))
            notes = sorted(
                (name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide[0-9]+\.xml", name)),
                key=lambda name: int(re.search(r"([0-9]+)\.xml$", name).group(1)))
            parts = []
            import html
            for index, name in enumerate(slides, 1):
                xml = z.read(name).decode("utf-8", "ignore")
                texts = [html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)]
                if texts:
                    parts.append(f"--- 幻灯片 {index} ---\n" + "\n".join(texts))
            for index, name in enumerate(notes, 1):
                xml = z.read(name).decode("utf-8", "ignore")
                texts = [html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)]
                if texts:
                    parts.append(f"--- 备注 {index} ---\n" + "\n".join(texts))
            return "\n\n".join(parts).strip() or None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def _read_pdf(path: Path) -> str | None:
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open(str(path))
        try:
            text = "\n".join(page.get_text("text") for page in doc)
        finally:
            doc.close()
        if text.strip():
            return text
    except Exception:
        pass
    executable = shutil.which("pdftotext")
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "-layout", str(path), "-"],
                                capture_output=True, timeout=45)
        if result.returncode == 0:
            return result.stdout.decode("utf-8", "replace").strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _soffice() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if Path(candidate).is_file():
            return candidate
    return None


def _read_libreoffice(path: Path) -> str | None:
    """PPT/DOC legacy conversion, cached by source mtime/size."""
    key = _cache_key(path)
    cached = _cache_dir() / f"{key}.txt"
    try:
        if cached.is_file():
            return cached.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        pass
    executable = _soffice()
    if not executable:
        return None
    with tempfile.TemporaryDirectory(prefix="mv_office_") as tmp:
        try:
            result = subprocess.run(
                [executable, "--headless", "--nologo", "--norestore", "--nolockcheck",
                 "--convert-to", "txt:Text", "--outdir", tmp, str(path)],
                capture_output=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        candidates = list(Path(tmp).glob("*.txt"))
        if not candidates:
            return None
        try:
            text = candidates[0].read_text(encoding="utf-8", errors="replace").strip()
            if text:
                cached.write_text(text, encoding="utf-8")
            return text or None
        except OSError:
            return None