"""Project-scoped meeting session ids for RAG / knowledge graph builds."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _norm(value: str) -> str:
    return str(Path(value).expanduser().resolve()).casefold()


def session_ids_for_project(project_root: Path | str, *, strict: bool = False) -> set[str]:
    """Return explicit archive/map membership, never the global session pool.

    Strict callers must not interpret unreadable or malformed membership as an
    empty project: errors propagate; absent optional files are still normal.
    The default retains the historical best-effort behavior.
    """
    root = Path(project_root).expanduser().resolve()
    ids: set[str] = set()
    minutes = root / "meetingvar" / "minutes"
    try:
        ids.update(child.name for child in minutes.iterdir() if child.is_dir())
    except FileNotFoundError:
        pass
    except OSError:
        if strict:
            raise
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "meetingVAR" / "sessions_map.json")
    candidates.append(root / "meetingvar" / "sessions_map.json")
    target = _norm(root)
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("session map must be an object")
            for sid, project in data.items():
                if not isinstance(sid, str) or not sid or not isinstance(project, str) or not project.strip():
                    if strict:
                        raise ValueError("invalid session membership entry")
                    continue
                if _norm(project) == target:
                    ids.add(sid)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            if strict:
                raise
    return ids
