"""Pruned project scanning + robust text extraction for project understanding."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
import zlib
from pathlib import Path

from .legacy_office import (extract_doc_text, extract_ppt_text, extract_rtf_text,
                            extract_xls_text)

SKIP_DIRS = {".git", ".venv", "venv", ".tools", ".runtimes", "node_modules",
             "__pycache__", "dist", "build", ".zcode", ".secrets"}

# Text-like files the agent may read. PDF/Office are converted to text.
SUPPORTED_TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".rst", ".log", ".rtf",
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xls",
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
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ARCHIVE_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"})
_TYPE_INFO = {
    "text": ("文本", "TXT", True),
    "pdf": ("PDF", "PDF", True),
    "docx": ("Word 文档", "DOCX", True),
    "rtf": ("RTF 文档", "RTF", True),
    "pptx": ("PowerPoint 演示文稿", "PPTX", True),
    "xlsx": ("Excel 工作簿", "XLSX", True),
    "doc": ("Word 97-2003 文档", "DOC", True),
    "ppt": ("PowerPoint 97-2003", "PPT", True),
    "xls": ("Excel 97-2003", "XLS", True),
    "zip": ("ZIP 压缩包", "ZIP", False),
    "image": ("图片", "IMG", False),
    "video": ("视频", "VIDEO", False),
    "audio": ("音频", "AUDIO", False),
    "ole": ("OLE 复合文档", "OLE", False),
    "binary": ("二进制文件", "BIN", False),
    "empty": ("空文件", "EMPTY", False),
}


def _read_head(path: Path, size: int = 64) -> bytes:
    try:
        with open(path, "rb") as stream:
            return stream.read(size)
    except OSError:
        return b""


def _zip_members_kind(path: Path) -> str:
    """Distinguish OOXML from a plain zip by central-directory member names."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if any(re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name) for name in names):
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def detect_file_type(path: Path, suffix: str | None = None) -> dict:
    """Classify by content first, extension second.

    A file named ``.txt`` whose bytes are a ZIP is a ZIP. Office files that were
    renamed still count as Office and remain readable; a plain archive is not
    text. ``mismatch`` is only set when the content type has a canonical
    extension that differs from the name.
    """
    path = Path(path)
    suffix = str(suffix if suffix is not None else path.suffix).lower()
    head = _read_head(path)
    if not head:
        kind, actual_suffix = "empty", suffix
    else:
        kind = ""
        actual_suffix = suffix
        if head[:4] in _ZIP_MAGICS:
            kind = _zip_members_kind(path)
            actual_suffix = {"docx": ".docx", "pptx": ".pptx", "xlsx": ".xlsx", "zip": ".zip"}[kind]
        elif head.startswith(b"%PDF-"):
            kind, actual_suffix = "pdf", ".pdf"
        elif head.startswith(b"{\\rtf"):
            kind, actual_suffix = "rtf", ".rtf"
        elif head[:8] == _OLE_MAGIC:
            kind = suffix[1:] if suffix in {".doc", ".ppt", ".xls"} else "ole"
            actual_suffix = "." + kind if kind in {"doc", "ppt", "xls"} else suffix
        elif head.startswith(b"\x89PNG\r\n\x1a\n"):
            kind, actual_suffix = "image", ".png"
        elif head.startswith((b"GIF87a", b"GIF89a")):
            kind, actual_suffix = "image", ".gif"
        elif head.startswith(b"\xff\xd8\xff"):
            kind, actual_suffix = "image", ".jpg"
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            kind, actual_suffix = "image", ".webp"
        elif head[:2] == b"BM":
            kind, actual_suffix = "image", ".bmp"
        elif head[:4] in (b"II*\x00", b"MM\x00*"):
            kind, actual_suffix = "image", ".tiff"
        elif head[:4] == b"\x00\x00\x01\x00":
            kind, actual_suffix = "image", ".ico"
        elif len(head) >= 12 and head[4:8] == b"ftyp":
            if head[8:12] in {b"M4A ", b"M4B "}:
                kind, actual_suffix = "audio", ".m4a"
            else:
                kind, actual_suffix = "video", ".mp4"
        elif head[:4] == b"\x1aE\xdf\xa3":
            kind, actual_suffix = "video", ".webm"
        elif head[:4] == b"RIFF" and head[8:12] == b"AVI ":
            kind, actual_suffix = "video", ".avi"
        elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            kind, actual_suffix = "audio", ".wav"
        elif head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] in {0xFB, 0xF3, 0xF2}):
            kind, actual_suffix = "audio", ".mp3"
        elif head[:4] == b"fLaC":
            kind, actual_suffix = "audio", ".flac"
        elif head[:4] == b"OggS":
            kind, actual_suffix = "audio", ".ogg"
        elif suffix == ".rtf":
            kind = "rtf"
        elif suffix in SUPPORTED_TEXT_SUFFIXES:
            kind = "text"
        elif suffix in _ARCHIVE_SUFFIXES:
            kind = "zip"
            actual_suffix = ".zip"
        else:
            kind = "binary"
    label, short, readable = _TYPE_INFO[kind]
    mismatch = kind not in {"text", "binary", "empty", "ole"} and suffix != actual_suffix
    return {"kind": kind, "label": label, "short": short, "readable": readable,
            "actual_suffix": actual_suffix, "mismatch": mismatch}


def zip_entries(path: Path, limit: int = 80) -> dict:
    """List archive members only; never extract them."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile):
        return {"count": 0, "entries": []}
    entries = [{"name": info.filename, "size": info.file_size,
                "dir": info.is_dir()} for info in infos[:limit]]
    return {"count": len(infos), "entries": entries}


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

    Content wins over extension: a renamed DOCX/PPTX is still parsed, while a
    plain ZIP named ``.txt`` stays binary. Plain text is decoded with
    BOM/UTF-8/GBK fallbacks. PDF uses PyMuPDF, then pypdf, then a standard
    library stream parser. Legacy DOC/PPT/XLS use pure-Python CFB parsers.
    No office suite or external converter is invoked.
    """
    path = Path(path)
    info = detect_file_type(path)
    kind = info["kind"]
    try:
        if kind == "docx":
            return _read_docx(path)
        if kind == "pptx":
            return _read_pptx(path)
        if kind == "pdf":
            return _read_pdf(path)
        if kind == "rtf":
            return extract_rtf_text(path)
        if kind in {"doc", "ppt", "xls", "ole"}:
            return _read_legacy_office(path)
        if kind != "text":
            return None
        data = path.read_bytes()[:max_bytes]
    except OSError:
        return None
    if not data:
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


def _pdf_unescape(raw: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(raw):
        ch = raw[index]
        if ch != 0x5C or index + 1 >= len(raw):
            out.append(ch)
            index += 1
            continue
        index += 1
        nxt = raw[index]
        mapping = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8,
                   ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
        if nxt in mapping:
            out.append(mapping[nxt])
            index += 1
        elif 48 <= nxt <= 55:
            digits = bytearray()
            while index < len(raw) and len(digits) < 3 and 48 <= raw[index] <= 55:
                digits.append(raw[index])
                index += 1
            out.append(int(digits.decode("ascii"), 8) & 0xFF)
        elif nxt in (10, 13):
            index += 1
            if nxt == 13 and index < len(raw) and raw[index] == 10:
                index += 1
        else:
            out.append(nxt)
            index += 1
    return bytes(out)


def _pdf_string(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def _read_pdf_basic(path: Path) -> str | None:
    """Last-resort PDF text reader using only the standard library.

    Handles Flate-compressed content streams and common Tj/TJ operators.  CID
    fonts without a usable ToUnicode map cannot be decoded; PyMuPDF/pypdf are
    preferred and are normal Python dependencies, not host applications.
    """
    data = path.read_bytes()
    chunks: list[str] = []
    for match in re.finditer(rb"(?<!end)stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            break
        raw = data[start:end].rstrip(b"\r\n")
        try:
            stream = zlib.decompress(raw)
        except zlib.error:
            continue
        if b"Tj" not in stream and b"TJ" not in stream and b"'" not in stream:
            continue
        text = []
        for show in re.finditer(
            rb"(?:\[(?:[^\[\]]|\\.)*\]\s*TJ|"
            rb"(?:\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>)\s*(?:Tj|'|\"))",
            stream, re.S,
        ):
            body = show.group(0)
            for literal in re.finditer(rb"\(((?:\\.|[^\\()])*)\)", body, re.S):
                value = _pdf_string(_pdf_unescape(literal.group(1)))
                if value:
                    text.append(value)
            for hex_string in re.finditer(rb"<([0-9A-Fa-f\s]+)>", body):
                digits = re.sub(rb"\s+", b"", hex_string.group(1))
                if len(digits) % 2:
                    digits += b"0"
                try:
                    value = _pdf_string(bytes.fromhex(digits.decode("ascii")))
                except ValueError:
                    continue
                if value:
                    text.append(value)
            if b"TJ" in body:
                text.append(" ")
        if text:
            chunks.append("".join(text))
    result = re.sub(r"[ \t]{2,}", " ", "\n".join(chunks)).strip()
    if not result:
        return None
    printable = sum(1 for ch in result if ch.isprintable() or ch in "\n\t")
    return result if printable / max(1, len(result)) > 0.75 else None


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
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if text.strip():
            return text
    except Exception:
        pass
    try:
        return _read_pdf_basic(path)
    except (OSError, ValueError, zlib.error):
        return None


def _read_legacy_office(path: Path) -> str | None:
    """Legacy PPT/DOC/XLS text with pure Python, cached by source mtime/size.

    No office suite is required or invoked.  The function name is retained for
    callers and caches created before 0.9.0.
    """
    key = _cache_key(path)
    cached = _cache_dir() / f"{key}.txt"
    try:
        if cached.is_file():
            return cached.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        pass
    text = None
    try:
        # Content is checked in order; each parser returns None for other formats.
        text = extract_doc_text(path)
        if not text:
            text = extract_ppt_text(path)
        if not text:
            text = extract_xls_text(path)
    except (OSError, ValueError):
        text = None
    if text:
        try:
            cached.write_text(text, encoding="utf-8")
        except OSError:
            pass
        return text
    return None