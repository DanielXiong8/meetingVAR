"""Per-source index state and a cross-process, crash-released project lock."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


@contextmanager
def project_singleflight(root: Path, owner_id: str = ""):
    """Nonblocking OS lock, plus thread guard (flock is process scoped).

    An owner label is informational only: reusing an operation id never bypasses
    a live lock. OS handles release on process death; recovery may safely retry.
    """
    root = Path(root).resolve()
    key = os.path.normcase(str(root))
    with _GUARD:
        guard = _LOCKS.setdefault(key, threading.Lock())
    if not guard.acquire(blocking=False):
        yield False
        return
    stream = None
    locked = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        stream = (root / "index.lock").open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        if locked:
            stream.seek(1)
            stream.write(json.dumps({"pid": os.getpid(), "owner_id": owner_id,
                                     "started_at": time.time()}).encode())
            stream.truncate()
            stream.flush()
        yield locked
    finally:
        if stream is not None:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        guard.release()


def ensure_registry(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS source_states (
        document_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
        status TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '', indexed_revision TEXT NOT NULL DEFAULT '',
        bytes_hash TEXT NOT NULL DEFAULT '', coverage TEXT NOT NULL DEFAULT '{}',
        errors TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL)""")


def set_source_state(connection, document: dict, *, status: str, revision: str = "",
                     indexed_revision: str = "", bytes_hash: str = "",
                     coverage: dict | None = None, errors: list | None = None) -> None:
    connection.execute("""INSERT INTO source_states
        (document_id,source_type,source_id,status,revision,indexed_revision,bytes_hash,coverage,errors,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET
        source_type=excluded.source_type,source_id=excluded.source_id,status=excluded.status,
        revision=excluded.revision,indexed_revision=excluded.indexed_revision,bytes_hash=excluded.bytes_hash,
        coverage=excluded.coverage,errors=excluded.errors,updated_at=excluded.updated_at""",
        (document["id"], document["source_type"], document["source_id"], status, revision,
         indexed_revision, bytes_hash, json.dumps(coverage or {}), json.dumps(errors or []), time.time()))


def source_states(connection) -> list[dict]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM source_states ORDER BY source_type,source_id")]
    for row in rows:
        row["coverage"] = json.loads(row["coverage"])
        row["errors"] = json.loads(row["errors"])
    return rows
