"""Structured desktop events; command-line consumers keep their readable output."""
from __future__ import annotations

import json
import os
import threading

PREFIX = "@@MEETINGVAR_EVENT "
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("MEETINGVAR_DESKTOP_EVENTS") == "1"


def emit(event_type: str, **fields) -> bool:
    if not enabled():
        return False
    message = PREFIX + json.dumps({"type": event_type, **fields}, ensure_ascii=True, allow_nan=False)
    with _LOCK:
        print(message, flush=True)
    return True
