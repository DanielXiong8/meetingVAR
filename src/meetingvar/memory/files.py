"""Pruned project scanning: installed runtimes are not project memory."""
from __future__ import annotations

import os
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", ".tools", ".runtimes", "node_modules",
             "__pycache__", "dist", "build", ".zcode"}


def project_files(root: Path, suffixes: set[str]):
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = sorted(name for name in children if name.lower() not in SKIP_DIRS
                             and not name.lower().startswith(".venv-")
                             and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() in suffixes and not path.is_symlink():
                yield path
