"""Local, coverage-aware extraction. Budgets never turn a partial read into success.

Revision = SHA256(source bytes + extractor version + semantic settings). Page
and time budgets are scheduling controls, not semantic settings, so increasing
a budget reuses successful pages. Legacy text-only caches are never trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

def read_source_ref(project_root: Path | str, ref: dict) -> dict:
    """Resolve a graph document reference; stale/deleted sources yield no text."""
    from .source_policy import excluded
    root = Path(project_root).resolve()
    try:
        lexical = root / str(ref["path"])
        if excluded(root, lexical, automatic=True):
            return {"text": "", "source_status": "unverifiable"}
        path = lexical.resolve()
        path.relative_to(root)
        if not ref.get("bytes_hash") or source_hash(path) != ref["bytes_hash"]:
            return {"text": "", "source_status": "stale"}
        result = read_document(path)
        if not result["complete"] or result["revision"] != ref.get("source_revision"):
            return {"text": "", "source_status": "unverifiable"}
        page = result["pages"][int(ref["page"]) - 1]
        if page["number"] != int(ref["page"]) or page["status"] not in _SUCCESS:
            return {"text": "", "source_status": "unverifiable"}
        start, end = int(ref["char_start"]), int(ref["char_end"])
        if not (0 <= start <= end <= len(page["text"])):
            return {"text": "", "source_status": "unverifiable"}
        return {**ref, "chunk_id": ref.get("id", ""), "text": page["text"][start:end],
                "source_status": "current", "source_type": "document",
                "source_id": str(ref["path"]), "title": path.name}
    except FileNotFoundError:
        return {"text": "", "source_status": "missing"}
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return {"text": "", "source_status": "unverifiable"}


EXTRACTOR_VERSION = "coverage-v1"
_SUCCESS = {"ok", "blank"}


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _error(code: str, page: int | None = None, message: str = "") -> dict:
    result = {"code": code}
    if page is not None:
        result["page"] = page
    if message:
        result["message"] = message[:300]
    return result


def _finish(result: dict) -> dict:
    pages = result["pages"]
    result["processed_pages"] = sum(p["status"] in _SUCCESS for p in pages)
    result["complete"] = (result.get("coverage_known", True) and not result["errors"]
                          and result["processed_pages"] == result["total_pages"])
    result["text"] = "\n\n".join(p["text"] for p in pages if p["status"] in _SUCCESS)
    result["coverage"] = {"total_pages": result["total_pages"],
                          "processed_pages": result["processed_pages"],
                          "failed_pages": [p["number"] for p in pages if p["status"] == "failed"],
                          "pending_pages": [p["number"] for p in pages if p["status"] == "pending"]}
    return result


def _checkpoint(path: Path, result: dict) -> None:
    """Atomic coverage checkpoint; no success-only .txt cache is ever written."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="coverage-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        pass  # Cache persistence is optional; coverage of the read is not.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _cached_pages(path: Path, revision: str, total: int) -> dict[int, dict]:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        pages = saved["pages"]
        if (saved.get("extractor_version") != EXTRACTOR_VERSION
                or saved.get("revision") != revision
                or saved.get("total_pages") != total
                or len(pages) != total
                or [p["number"] for p in pages] != list(range(1, total + 1))):
            return {}
        return {p["number"]: p for p in pages
                if p.get("status") in _SUCCESS and isinstance(p.get("text"), str)
                and p.get("method") in {"text", "ocr"}
                and (p["status"] != "blank" or not p["text"].strip())}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _ocr_page(page, fitz, settings: dict, timeout: float) -> str:
    from . import files
    executable = files._find_tesseract()
    if not executable:
        raise RuntimeError("ocr_unavailable")
    with tempfile.TemporaryDirectory(prefix="mv-ocr-") as temp:
        image = Path(temp) / "page.png"
        page.get_pixmap(dpi=settings["dpi"], colorspace=fitz.csRGB).save(str(image))
        reply = files.subprocess.run(
            [executable, str(image), "stdout", "-l", settings["languages"],
             "--psm", str(settings["psm"])],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)
        if reply.returncode != 0:
            raise RuntimeError(f"ocr_exit_{reply.returncode}")
        return files._clean_ocr_text(reply.stdout or "")


def read_document(path: Path | str, *, max_bytes: int = 8_000_000,
                  max_pages: int | None = None, timeout: float | None = None,
                  ocr_timeout: float = 60, dpi: int = 160, languages: str = "chi_sim+eng",
                  psm: int = 6, force_ocr: bool = False, progress=None) -> dict:
    """Read locally and report exact coverage. 0/None budgets mean unlimited.

    ``processed_pages`` counts successful (including verified blank) pages, not
    attempts. Failed and pending pages alone are retried on the next call. Text
    callers may use read_text_safely; pipelines MUST inspect ``complete`` here.
    """
    from . import files
    path = Path(path)
    settings = {"dpi": int(dpi), "languages": str(languages), "psm": int(psm),
                "force_ocr": bool(force_ocr)}
    result = {"text": "", "pages": [], "complete": False, "errors": [],
              "revision": "", "bytes_hash": "", "total_pages": 0,
              "processed_pages": 0, "extractor_version": EXTRACTOR_VERSION,
              "settings": settings, "coverage_known": False}
    try:
        info = path.stat()
        digest = source_hash(path)
        result["bytes_hash"] = digest
        result["revision"] = hashlib.sha256((digest + EXTRACTOR_VERSION +
            json.dumps(settings, sort_keys=True)).encode()).hexdigest()
    except OSError as exc:
        result["errors"].append(_error("source_unreadable", message=type(exc).__name__))
        return _finish(result)
    if max_pages is None:
        try:
            max_pages = max(0, int(os.environ.get("MEETINGVAR_OCR_MAX_PAGES", "0")))
        except ValueError:
            max_pages = 0
    max_pages = max(0, int(max_pages or 0))
    deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
    kind = files.detect_file_type(path)["kind"]
    if kind != "pdf":
        result["total_pages"] = 1
        result["coverage_known"] = True
        try:
            text = "" if info.st_size == 0 else files._read_text_legacy(path, max_bytes=max_bytes)
            truncated = kind == "text" and info.st_size > max_bytes
            timed_out = deadline is not None and time.monotonic() >= deadline
            if text is None:
                raise ValueError("unsupported_or_unreadable")
            status = "pending" if truncated or timed_out else ("ok" if text.strip() else "blank")
            result["pages"] = [{"number": 1, "text": text, "status": status, "method": "text"}]
            if truncated:
                result["errors"].append(_error("byte_limit", 1))
            if timed_out:
                result["errors"].append(_error("time_limit", 1))
            if source_hash(path) != digest:
                result["errors"].append(_error("source_changed_during_read"))
        except Exception as exc:
            result["pages"] = [{"number": 1, "text": "", "status": "failed", "method": "text"}]
            result["errors"].append(_error("extraction_failed", 1, str(exc)))
        _finish(result)
        # Legacy string readers may display a preview; structured callers cannot
        # confuse this preview with a full read.
        if result["pages"][0]["status"] == "pending":
            result["text"] = result["pages"][0]["text"]
        return result
    if info.st_size > 200_000_000:
        result["errors"].append(_error("source_too_large"))
        return _finish(result)
    document = None
    fitz = None
    try:
        try:
            import fitz
        except ImportError:
            import pymupdf as fitz
        document = fitz.open(str(path))
        if getattr(document, "needs_pass", False):
            raise ValueError("password_required")
        total = len(document)
        backend = "fitz"
    except Exception as exc:
        if document is not None:
            document.close()
            document = None
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            total = len(reader.pages)
            backend = "pypdf"
        except Exception:
            result["errors"].append(_error("pdf_open_failed", message=type(exc).__name__))
            _finish(result)
            try:
                result["text"] = files._read_pdf_basic(path) or ""
            except Exception:
                pass
            # Stream parsing cannot establish page boundaries or full coverage.
            return result
    result["total_pages"] = total
    result["coverage_known"] = True
    cache = files._cache_dir() / (result["revision"] + "-coverage.json")
    reused = _cached_pages(cache, result["revision"], total)
    result["pages"] = [dict(reused.get(n, {"number": n, "text": "",
                         "status": "pending", "method": "text"})) for n in range(1, total + 1)]
    try:
        for index in range(total):
            entry = result["pages"][index]
            number = index + 1
            if entry["status"] in _SUCCESS:
                continue
            if max_pages and number > max_pages:
                result["errors"].append(_error("page_limit", number))
                continue
            if deadline is not None and time.monotonic() >= deadline:
                result["errors"].append(_error("time_limit", number))
                continue
            try:
                if backend == "fitz":
                    page = document[index]
                    text_failed = False
                    try:
                        text = "" if force_ocr else page.get_text("text")
                    except Exception:
                        text = ""
                        text_failed = True
                    if not str(text or "").strip():
                        # Only a successfully inspected empty page is verified blank.
                        blank = (not force_ocr and not text_failed and hasattr(page, "get_images")
                                 and hasattr(page, "get_drawings") and not page.get_images()
                                 and not page.get_drawings())
                        if not blank:
                            entry["method"] = "ocr"
                            remaining = max(0.001, deadline - time.monotonic()) if deadline else ocr_timeout
                            text = _ocr_page(page, fitz, settings, min(ocr_timeout, remaining))
                else:
                    text = reader.pages[index].extract_text() or ""
                    if force_ocr or not text.strip():
                        entry["method"] = "ocr"
                        raise RuntimeError("ocr_renderer_unavailable")
                if deadline is not None and time.monotonic() >= deadline:
                    entry.update(text="", status="pending")
                    result["errors"].append(_error("time_limit", number))
                else:
                    entry.update(text=str(text or ""), status="ok" if str(text or "").strip() else "blank")
            except Exception as exc:
                entry.update(text="", status="failed")
                code = "ocr_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "page_failed"
                result["errors"].append(_error(code, number, str(exc)))
            _finish(result)
            _checkpoint(cache, result)
            if callable(progress):
                progress({"current": str(path), "page": number,
                          "processed": result["processed_pages"], "total": total})
    finally:
        if document is not None:
            document.close()
    try:
        if source_hash(path) != digest:
            result["errors"].append(_error("source_changed_during_read"))
            cache.unlink(missing_ok=True)
            return _finish(result)
    except OSError:
        result["errors"].append(_error("source_changed_during_read"))
        cache.unlink(missing_ok=True)
        return _finish(result)
    _finish(result)
    _checkpoint(cache, result)
    return result
