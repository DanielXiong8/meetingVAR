"""Local process ownership probes; no task execution, discovery or background threads.

A denied/unsupported probe is unknown, never proof of death. Windows uses a
process handle plus creation FILETIME (not kill(pid, 0), which is unsafe there).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import uuid
from pathlib import Path

_OWNER_LOCK = threading.Lock()
_OWNER = None


def process_identity(pid: int) -> tuple[str, str]:
    """Return (alive/dead/unknown, stable start identity when available)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "unknown", ""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        if pid > 0xffffffff:
            return "unknown", ""
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER: PID no longer exists. Access denied is unknown.
            return ("dead" if ctypes.get_last_error() == 87 else "unknown"), ""
        try:
            wait = kernel.WaitForSingleObject(handle, 0)
            if wait == 0:
                return "dead", ""
            if wait != 258:  # WAIT_TIMEOUT means the process has not exited.
                return "unknown", ""
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return "alive", ""
            start = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return "alive", "windows:" + str(start)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            # Field 2 may contain spaces and parentheses; field 22 is starttime.
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] in {"Z", "X"}:
                return "dead", ""
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return "alive", f"linux:{boot}:{stat[19]}"
        except ProcessLookupError:
            return "dead", ""
        except (OSError, ValueError, IndexError):
            pass
    try:
        os.kill(pid, 0)  # POSIX only; no signal is delivered.
        return "alive", ""
    except ProcessLookupError:
        return "dead", ""
    except (OSError, OverflowError):
        return "unknown", ""


def current_owner() -> dict:
    """Process-wide identity shared by store instances, refreshed after a fork."""
    global _OWNER
    pid = os.getpid()
    with _OWNER_LOCK:
        if _OWNER is None or _OWNER["owner_pid"] != pid:
            _, start = process_identity(pid)
            _OWNER = {"owner_pid": pid, "owner_start": start,
                      "owner_host": socket.gethostname().casefold(),
                      "owner_id": uuid.uuid4().hex}
        return dict(_OWNER)


def owner_identity(pid: int | None = None) -> dict:
    """Obtain a local live process identity for explicit trusted host delegation.

    Desktop should export owner_pid and owner_start as
    MEETINGVAR_OPERATION_OWNER_PID and MEETINGVAR_OPERATION_OWNER_START in its
    bridge child's environment. This is provenance, not a security boundary.
    """
    if pid is None or pid == os.getpid():
        return current_owner()
    state, start = process_identity(pid)
    if state != "alive" or not start:
        raise ValueError("不能核验委托宿主进程身份")
    host = current_owner()["owner_host"]
    return {"owner_pid": pid, "owner_start": start, "owner_host": host,
            "owner_id": uuid.uuid5(uuid.NAMESPACE_OID, f"{host}:{pid}:{start}").hex}


def resolve_owner(owner=None) -> dict:
    """Validate explicit/environment delegation; never silently fall back."""
    if owner is None:
        pid = os.environ.get("MEETINGVAR_OPERATION_OWNER_PID")
        start = os.environ.get("MEETINGVAR_OPERATION_OWNER_START")
        if pid is None and start is None:
            return current_owner()
        if not pid or not start or not pid.isdecimal():
            raise ValueError("宿主 owner 环境变量不完整或无效")
        owner = {"owner_pid": int(pid), "owner_start": start}
    if not isinstance(owner, dict):
        raise ValueError("owner 必须为结构化进程身份")
    pid, start = owner.get("owner_pid"), owner.get("owner_start")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not start:
        raise ValueError("owner 缺少 pid 或进程启动身份")
    actual = owner_identity(pid)
    state, actual_start = process_identity(pid)
    if (state != "alive" or not actual_start or start != actual_start
            or owner.get("owner_host", actual["owner_host"]) != actual["owner_host"]):
        raise ValueError("宿主 owner 已退出或进程身份不匹配")
    return actual


def owns(row: dict, owner: dict) -> bool:
    if (row.get("owner_pid"), row.get("owner_host")) != (owner["owner_pid"], owner["owner_host"]):
        return False
    if row.get("owner_start") and owner["owner_start"]:
        return row["owner_start"] == owner["owner_start"]
    return bool(row.get("owner_id") and row["owner_id"] == owner["owner_id"])


def orphan_reason(row: dict, now: float) -> str | None:
    """Protect proven-live/foreign-host owners; only dead or expired can recover.

    Legacy rows with neither owner nor lease remain untouched. A locally proven
    live owner wins over an expired lease so existing long-running callers do
    not need a new heartbeat thread. An unobservable local owner can expire.
    """
    host = row.get("owner_host")
    if host and host != current_owner()["owner_host"]:
        return None  # This machine cannot establish a remote owner's liveness.
    pid = row.get("owner_pid")
    if pid and host:
        state, start = process_identity(pid)
        if state == "dead":
            return "owner_dead"
        if state == "alive":
            if start and row.get("owner_start") and start != row["owner_start"]:
                return "owner_replaced"
            return None
    expires = row.get("lease_expires_at")
    if isinstance(expires, (int, float)) and 0 < expires <= now:
        return "lease_expired"
    return None
