"""Agent skills built around the product's evidence moat.

- EvidencePackSkill: turn a clip into a portable, hash-verifiable evidence ZIP.
- CommitmentLedgerSkill: show what was promised, by whom, and what changed.
- FindMomentSkill: locate the exact 30-90 second window behind a question.

All three are deterministic first; LLM is only optional for prose.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from .. import config
from ..rag import RagIndex
from .memory import FactStore
from .sessions import SessionCatalog

_SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _fmt(sec: float | None) -> str:
    total = max(0, int(float(sec or 0)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_dir_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "evidence"))[:80]


class EvidencePackSkill:
    def __init__(self, project_root: Path, data_dir: Path | None = None):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.catalog = SessionCatalog(self.data_dir)

    def _clip_files(self, session_id: str) -> list[dict]:
        pool = self.data_dir / "pool" / session_id
        rows = []
        if not pool.is_dir():
            return rows
        for path in sorted(pool.glob("*.json")):
            if path.name == "transcript.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("trigger") and data.get("clip_id"):
                rows.append(data)
        return rows

    def _latest_session(self) -> str:
        for row in self.catalog.list_sessions(limit=50):
            if self._clip_files(row["session_id"]):
                return row["session_id"]
        return ""

    def _resolve_clip(self, session_id: str = "", clip_id: str = "",
                      query: str = "") -> tuple[str, dict]:
        if session_id and not _SAFE_ID.fullmatch(session_id):
            raise ValueError("invalid session id")
        sessions = [session_id] if session_id else [
            row["session_id"] for row in self.catalog.list_sessions(limit=50)]
        if query:
            found = FindMomentSkill(self.project_root, self.data_dir).run(query, limit=1)
            if found.get("segments"):
                segment = found["segments"][0]
                session_id = segment.get("session_id", session_id)
                clip_id = segment.get("clip_id", clip_id)
        for sid in sessions:
            rows = self._clip_files(sid)
            if clip_id:
                for row in rows:
                    if row.get("clip_id") == clip_id:
                        return sid, row
            elif rows:
                return sid, rows[-1]
        raise FileNotFoundError("no clip found")

    @staticmethod
    def _source_path(data_dir: Path, raw: str, *, base: str = "clips") -> Path | None:
        if not raw:
            return None
        path = Path(raw)
        candidate = data_dir.parent / path if path.parts and path.parts[0] == "data" else data_dir / base / path
        try:
            candidate = candidate.resolve()
            root = data_dir.resolve()
            if root not in candidate.parents and candidate != root:
                return None
        except OSError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
        candidates = [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ]
        for exe in candidates:
            if not exe.is_file():
                continue
            with tempfile.TemporaryDirectory(prefix="mv_pack_pdf_") as profile:
                try:
                    result = subprocess.run(
                        [str(exe), "--headless=new", "--disable-gpu", "--no-sandbox",
                         f"--user-data-dir={profile}", "--no-pdf-header-footer",
                         f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                        capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    continue
                if pdf_path.is_file():
                    return True
        return False

    def propose(self, query: str = "", session_id: str = "", clip_id: str = "") -> dict:
        session, clip = self._resolve_clip(session_id, clip_id, query)
        trigger = clip.get("trigger") or {}
        return {"session_id": session, "clip_id": clip.get("clip_id", ""),
                "title": f"{session} · {trigger.get('evidence', '证据片段')}",
                "trigger": trigger.get("evidence", ""),
                "at": trigger.get("at", clip.get("audio", {}).get("start_ts", 0))}

    def run(self, *, session_id: str = "", clip_id: str = "", query: str = "",
            out_dir: Path | None = None) -> dict:
        session_id, clip = self._resolve_clip(session_id, clip_id, query)
        trigger = clip.get("trigger") or {}
        audio = clip.get("audio") or {}
        screen = clip.get("screen") or {}
        transcript = clip.get("transcript") or []
        root = Path(out_dir) if out_dir else self.project_root / "meetingvar" / "evidence_packs"
        name = f"{_safe_dir_name(session_id)}_{str(clip.get('clip_id', ''))[:8]}"
        target = root / name
        if target.exists():
            target = root / f"{name}_{int(time.time())}"
        target.mkdir(parents=True, exist_ok=True)

        audio_path = self._source_path(self.data_dir, audio.get("path", ""), base="clips")
        if audio_path:
            shutil.copy2(audio_path, target / "audio.wav")
        frames = []
        for raw in (screen.get("keyframes") or [])[:12]:
            source = self._source_path(self.data_dir, raw, base="clips")
            if source:
                destination = target / "screens" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                frames.append(destination.relative_to(target).as_posix())
        transcript_text = "\n".join(
            f"[{_fmt(line.get('ts'))}] {line.get('speaker', '')}: {line.get('text', '')}"
            for line in transcript)
        (target / "transcript.txt").write_text(transcript_text + "\n", encoding="utf-8")

        quotes = [line.get("text", "") for line in transcript if line.get("text")][:12]
        summary_md = "\n".join([
            f"# 证据包 · {session_id}", "",
            f"- 片段：`{clip.get('clip_id', '')}`",
            f"- 触发：{trigger.get('layer', '')} / {trigger.get('evidence', '')}",
            f"- 时间窗：{_fmt(audio.get('start_ts'))} – {_fmt(audio.get('end_ts'))}",
            f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
            "## 关键原话", "",
            *[f"- “{quote}”" for quote in quotes],
            "", "## 文件", "",
            "- `audio.wav`：命中窗口音频",
            "- `transcript.txt`：同窗口转写",
            f"- `screens/`：{len(frames)} 张关键屏幕帧",
            "- `manifest.json` / `SHA256SUMS.txt`：来源与完整性校验",
        ]) + "\n"
        (target / "summary.md").write_text(summary_md, encoding="utf-8")
        summary_html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>meetingVAR 证据包</title><style>
body{{font-family:-apple-system,'Segoe UI',sans-serif;max-width:760px;margin:40px auto;line-height:1.7;color:#1e2430}}
h1{{font-size:26px}}h2{{font-size:18px;margin-top:28px}}code{{background:#f0f2f5;padding:2px 5px;border-radius:4px}}
.quote{{border-left:3px solid #2f6fed;padding-left:12px;color:#38445a}}
</style></head><body>
<h1>meetingVAR 证据包</h1>
<p><b>会议：</b>{html.escape(session_id)}<br><b>片段：</b><code>{html.escape(str(clip.get('clip_id', '')))}</code><br>
<b>触发：</b>{html.escape(str(trigger.get('layer', '')))} / {html.escape(str(trigger.get('evidence', '')))}<br>
<b>时间窗：</b>{_fmt(audio.get('start_ts'))} – {_fmt(audio.get('end_ts'))}</p>
<h2>关键原话</h2>{''.join(f'<p class="quote">{html.escape(q)}</p>' for q in quotes)}
<h2>完整性</h2><p>本包包含 manifest 与 SHA256SUMS；可用 <code>certutil -hashfile</code> 或 <code>sha256sum -c</code> 校验。</p>
</body></html>"""
        (target / "summary.html").write_text(summary_html, encoding="utf-8")
        self._html_to_pdf(target / "summary.html", target / "summary.pdf")

        files = sorted(path for path in target.rglob("*") if path.is_file())
        manifest = {
            "version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": session_id,
            "clip_id": clip.get("clip_id", ""),
            "trigger": trigger,
            "window": {"start": audio.get("start_ts"), "end": audio.get("end_ts")},
            "transcript": transcript,
            "files": [{"path": path.relative_to(target).as_posix(), "sha256": _sha256(path)}
                      for path in files],
            "consent": "local-capture",
            "generator": "meetingVAR",
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        sums = []
        for path in sorted(target.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                sums.append(f"{_sha256(path)}  {path.relative_to(target).as_posix()}")
        (target / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
        archive = target.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(target).as_posix())
        return {"ok": True, "zip_path": str(archive), "directory": str(target),
                "manifest": manifest, "files": len(files), "quotes": len(quotes)}


class CommitmentLedgerSkill:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.store = FactStore(self.project_root)

    def run(self, query: str = "") -> dict:
        facts = self.store.list_facts(limit=2000, status="all", include_inactive=True)
        groups: dict[str, list[dict]] = {}
        for fact in facts:
            if fact.get("type") not in {"commitment", "action-item", "decision"}:
                continue
            key = str(fact.get("canonical_key") or
                      f"{fact.get('type')}:{fact.get('description', '')}")
            groups.setdefault(key, []).append(fact)
        rows = []
        for group in groups.values():
            current = max(group, key=lambda fact: (str(fact.get("created_at", "")),
                                                    int(fact.get("version", 1))))
            history = []
            for fact in group:
                try:
                    history.extend(self.store.history(str(fact.get("name", ""))))
                except (OSError, ValueError):
                    history.append(fact)
            unique = {int(fact.get("version", 1)): fact for fact in history}
            group = [unique[key] for key in sorted(unique)] or group
            group.sort(key=lambda fact: int(fact.get("version", 1)))
            text = " ".join(str(current.get(key, "")) for key in
                            ("name", "description", "body", "owner", "due"))
            if query and query.casefold() not in text.casefold():
                continue
            changes = []
            for previous, later in zip(group, group[1:]):
                for field in ("owner", "due"):
                    before, after = str(previous.get(field, "")), str(later.get(field, ""))
                    if before != after:
                        changes.append({"field": field, "from": before, "to": after})
                if str(previous.get("body", "")) != str(later.get("body", "")):
                    changes.append({"field": "body", "from": "上一版", "to": "已更新"})
            rows.append({
                "title": current.get("description") or current.get("name", ""),
                "type": current.get("type", ""),
                "owner": current.get("owner", ""),
                "due": current.get("due", ""),
                "status": current.get("status", "active"),
                "first_seen": group[0].get("created_at", ""),
                "last_seen": current.get("updated_at", ""),
                "version": current.get("version", 1),
                "changes": changes[-8:],
                "evidence": list(current.get("source_refs") or [])[:8],
                "name": current.get("name", ""),
            })
        rows.sort(key=lambda row: (row["status"] != "active", row["title"]))
        lines = ["承诺账本", ""]
        for row in rows:
            suffix = f"（{row['owner']} / {row['due']}）" if row["owner"] or row["due"] else ""
            lines.append(f"- [{row['status']}] {row['title']}{suffix}")
            for change in row["changes"]:
                lines.append(f"  - {change['field']}: {change['from'] or '空'} → {change['to'] or '空'}")
        return {"ok": True, "rows": rows, "answer": "\n".join(lines),
                "citations": [{"kind": "fact", "name": row["name"]}
                              for row in rows[:20] if row.get("name")],
                "mode": "skill"}


class FindMomentSkill:
    def __init__(self, project_root: Path, data_dir: Path | None = None, limit: int = 5):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.catalog = SessionCatalog(self.data_dir)
        self.limit = max(1, min(10, int(limit)))

    def _clip_near(self, session_id: str, start: float, end: float) -> str:
        pool = self.data_dir / "pool" / session_id
        for path in pool.glob("*.json") if pool.is_dir() else []:
            if path.name == "transcript.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            at = float((data.get("trigger") or {}).get("at") or
                       (data.get("audio") or {}).get("start_ts") or 0)
            if start - 120 <= at <= end + 120:
                return str(data.get("clip_id", ""))
        return ""

    def run(self, query: str, *, session_id: str = "", limit: int | None = None) -> dict:
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "query_required", "segments": []}
        count = max(1, min(10, int(limit or self.limit)))
        segments = []
        try:
            rag = RagIndex(self.project_root, self.data_dir)
            chunks = rag.search(query, limit=count, source_type="meeting", rerank=False)
        except Exception:
            chunks = []
        for chunk in chunks:
            sid = str(chunk.get("source_id") or "")
            if session_id and sid != session_id:
                continue
            start = float(chunk.get("start_ts") or 0)
            end = float(chunk.get("end_ts") or start + 60)
            segments.append({
                "session_id": sid, "start": start, "end": end,
                "text": str(chunk.get("text", ""))[:1600],
                "turn_ids": list(chunk.get("turn_ids") or []),
                "score": chunk.get("score", 0), "why": chunk.get("why", []),
                "clip_id": self._clip_near(sid, start, end),
            })
        if not segments:
            for row in self.catalog.search(query, limit=count, per_session=4):
                sid = row["session_id"]
                if session_id and sid != session_id:
                    continue
                for match in row.get("matches", [])[:2]:
                    ts = float(match.get("ts", 0))
                    segments.append({
                        "session_id": sid, "start": max(0, ts - 30), "end": ts + 45,
                        "text": f"[{match.get('id')}] {match.get('speaker', '')}: "
                                f"{match.get('text', '')}",
                        "turn_ids": [match.get("id", "")], "score": match.get("score", 0),
                        "why": [query], "clip_id": self._clip_near(sid, ts - 30, ts + 45),
                    })
        deduped, seen = [], set()
        for segment in sorted(segments, key=lambda item: -float(item.get("score") or 0)):
            key = (segment["session_id"], int(segment["start"] // 60))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(segment)
        if not deduped:
            return {"ok": True, "segments": [], "answer": "没有找到匹配片段。",
                    "citations": [], "mode": "skill"}
        lines = ["找到以下片段：", ""]
        for segment in deduped[:count]:
            lines.append(f"- {segment['session_id']} {_fmt(segment['start'])}–"
                         f"{_fmt(segment['end'])}"
                         f"{'（clip ' + segment['clip_id'][:8] + '）' if segment['clip_id'] else ''}")
            lines.append(f"  {segment['text'][:220]}")
        return {"ok": True, "segments": deduped[:count], "answer": "\n".join(lines),
                "citations": [{"kind": "turn", "session_id": s["session_id"],
                               "turn_ids": s["turn_ids"][:4], "quote": ""}
                              for s in deduped[:count] if s.get("turn_ids")],
                "mode": "skill"}