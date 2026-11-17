"""Create real DOCX/PPTX/PDF files inside the selected project.

The text editor path in :mod:`file_ops` intentionally only writes UTF-8 text.
This module adds binary document generation while keeping the same safety
boundary: project-root only, explicit user confirmation, backup on overwrite.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .file_ops import _safe_target

DOCUMENT_SUFFIXES = {".docx", ".pptx", ".pdf"}
MAX_DOCUMENT_CHARS = 200_000


def validate_document_write(project_root: Path | str, relative: str,
                            content: str) -> tuple[Path, str, str]:
    target, rel = _safe_target(project_root, relative)
    if target.suffix.lower() not in DOCUMENT_SUFFIXES:
        raise ValueError("只支持生成 .docx / .pptx / .pdf")
    text = str(content if content is not None else "")
    if "\x00" in text:
        raise ValueError("内容包含二进制字符")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise ValueError(f"内容超过 {MAX_DOCUMENT_CHARS} 字符上限")
    if target.exists() and target.is_symlink():
        raise ValueError("拒绝写入符号链接")
    return target, rel, text


def _backup(project_root: Path, target: Path, rel: str) -> str:
    if not target.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = project_root / "meetingvar" / "agent_memory" / "file_backups" / stamp
    backup_target = backup_dir / rel
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_target)
    return f"meetingvar/agent_memory/file_backups/{stamp}/{rel}"


def _find_soffice() -> str:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in (
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe")):
        if candidate.is_file():
            return str(candidate)
    return ""


def _html_from_text(text: str, title: str) -> str:
    """Small Markdown subset: headings, bullets, paragraphs and page breaks."""
    blocks: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            blocks.append("</ul>")
            list_open = False

    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        if stripped in {"---", "***", "___"}:
            close_list()
            blocks.append('<div class="pagebreak"></div>')
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"^[-*•]\s+(.+)$", stripped)
        if bullet:
            if not list_open:
                blocks.append("<ul>")
                list_open = True
            blocks.append(f"<li>{html.escape(bullet.group(1))}</li>")
            continue
        close_list()
        blocks.append(f"<p>{html.escape(stripped)}</p>")
    close_list()
    body = "\n".join(blocks)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: "Microsoft YaHei", "SimSun", sans-serif; font-size: 12pt;
       line-height: 1.7; margin: 42px 48px; color: #222; }}
h1 {{ font-size: 22pt; margin: 0 0 18pt; }}
h2 {{ font-size: 16pt; margin: 18pt 0 8pt; }}
h3 {{ font-size: 13pt; margin: 14pt 0 6pt; }}
p {{ margin: 6pt 0; }}
li {{ margin: 3pt 0; }}
.pagebreak {{ page-break-after: always; }}
</style></head><body>
{body}
</body></html>"""


def _convert_with_libreoffice(source: Path, suffix: str, target: Path) -> None:
    soffice = _find_soffice()
    if not soffice:
        raise ValueError("未找到 LibreOffice，无法生成 DOCX/PDF；请安装 LibreOffice 后重试")
    with tempfile.TemporaryDirectory(prefix="mv-doc-") as temp:
        temp_path = Path(temp)
        profile = temp_path / "profile"
        output = temp_path / "out"
        output.mkdir()
        command = [
            soffice, "--headless", "--nologo", "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to", 'docx:MS Word 2007 XML' if suffix == ".docx" else "pdf",
            "--outdir", str(output), str(source),
        ]
        flags = 0x08000000 if os.name == "nt" else 0
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=90, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise ValueError("LibreOffice 转换超时（90 秒）") from None
        produced = output / f"{source.stem}{suffix}"
        if not produced.is_file():
            detail = (proc.stderr or proc.stdout or "").strip()[-300:]
            raise ValueError("LibreOffice 未能生成文件" + (f"：{detail}" if detail else ""))
        shutil.copy2(produced, target)


def _create_pptx(content: str, target: Path, title: str) -> None:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise ValueError("PPTX 生成需要 python-pptx") from exc
    text = str(content or "").strip()
    # Prefer explicit slide separators; otherwise split at level-2 headings.
    slides = [part.strip() for part in re.split(r"\n\s*---+\s*\n", text) if part.strip()]
    if len(slides) <= 1:
        parts = re.split(r"(?m)^##\s+", text)
        if len(parts) > 1:
            slides = [("## " + part).strip() for part in parts if part.strip()]
    if not slides:
        slides = [f"# {title}\n- 暂无内容"]
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    for index, part in enumerate(slides[:80]):
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        lines = [line.strip() for line in part.splitlines() if line.strip()]
        heading = ""
        if lines and lines[0].startswith("#"):
            heading = lines.pop(0).lstrip("#").strip()
        elif index == 0:
            heading = title
        else:
            heading = f"第 {index + 1} 页"
        heading = re.sub(r"^#+\s*", "", heading).strip()
        slide.shapes.title.text = heading
        body = slide.placeholders[1].text_frame
        body.clear()
        for line_index, line in enumerate(lines[:18]):
            paragraph = body.paragraphs[0] if line_index == 0 else body.add_paragraph()
            paragraph.text = line.lstrip("-*• ").strip()
            paragraph.level = 0
            for run in paragraph.runs:
                run.font.size = Pt(18)
                run.font.name = "Microsoft YaHei"
    presentation.save(str(target))


def create_document(project_root: Path | str, relative: str, content: str) -> dict:
    root = Path(project_root).resolve()
    target, rel, text = validate_document_write(root, relative, content)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(root, target, rel)
    temporary = target.with_name(f"mv-{uuid.uuid4().hex}{target.suffix}")
    suffix = target.suffix.lower()
    try:
        if suffix == ".pptx":
            _create_pptx(text, temporary, target.stem)
        else:
            html_path = temporary.with_suffix(".html")
            html_path.write_text(_html_from_text(text, target.stem), encoding="utf-8")
            try:
                _convert_with_libreoffice(html_path, suffix, temporary)
            finally:
                try:
                    html_path.unlink()
                except OSError:
                    pass
        if not temporary.is_file():
            raise ValueError("文档生成失败")
        os.replace(temporary, target)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
    return {"path": rel, "bytes": target.stat().st_size, "kind": suffix.lstrip("."),
            "backup": backup}