#!/usr/bin/env python3
"""Report what the CodeArts / OfficeAce skill runtime actually supports.

Read-only except for a small temporary write test. No network calls.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def cmd_probe(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"found": False}
    result = {"found": True, "path": path}
    for flag in ("-version", "--version"):
        try:
            completed = subprocess.run(
                [path, flag], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            continue
        text = (completed.stdout or completed.stderr or "").strip()
        if text:
            result["version"] = text.splitlines()[0][:200]
            break
    return result


def module_probe(name: str) -> dict:
    try:
        module = __import__(name)
    except Exception as exc:  # noqa: BLE001 - probe reports any import failure
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"ok": True, "version": str(getattr(module, "__version__", ""))[:80]}


def write_probe() -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="probe_") as temporary:
            target = Path(temporary) / "probe.txt"
            target.write_text("runtime-probe", encoding="utf-8")
            ok = target.read_text(encoding="utf-8") == "runtime-probe"
        return {"ok": ok, "dir": "system temp"}
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def subprocess_probe() -> dict:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", "print('subprocess-ok')"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        return {
            "ok": completed.returncode == 0
            and "subprocess-ok" in (completed.stdout or ""),
            "returncode": completed.returncode,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def hash_probe() -> dict:
    digest = hashlib.sha256(b"runtime-probe").hexdigest()
    expected = "93e35b6f369d7223f6dea7e5cb66e758efef103aef8cf2815597c5e68d9b2c98"
    return {"ok": digest == expected, "sha256_prefix": digest[:12]}


def main() -> int:
    payload = {
        "probe": "runtime-probe",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": {
            "version": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
            "prefix": sys.prefix,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "cwd": str(Path.cwd()),
        "ffmpeg": cmd_probe("ffmpeg"),
        "ffprobe": cmd_probe("ffprobe"),
        "modules": {
            "faster_whisper": module_probe("faster_whisper"),
            "av": module_probe("av"),
            "numpy": module_probe("numpy"),
        },
        "write": write_probe(),
        "subprocess": subprocess_probe(),
        "hashlib": hash_probe(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())