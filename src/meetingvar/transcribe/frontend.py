"""Audio front-end for lecture/meeting replay: normalize before ASR.

The original recording is preserved.  The processed WAV is written to a caller
provided path (usually a temp file) and is deleted by the caller.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _ffmpeg() -> str | None:
    from ..capture.hd import _ffmpeg as find

    return find()


def preprocess_wav(source: str | Path, target: str | Path, *,
                   denoise: bool = True, normalize: bool = True) -> bool:
    """Create a 16 kHz mono WAV with optional denoise/loudness normalization."""
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return False
    filters = []
    if denoise:
        # afftdn is available in the ffmpeg build shipped by imageio-ffmpeg.
        filters.extend(["highpass=f=80", "afftdn=nf=-25"])
    if normalize:
        filters.append("loudnorm=I=-18:TP=-2:LRA=11")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
           "-vn", "-ac", "1", "-ar", "16000"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-c:a", "pcm_s16le", str(target)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and Path(target).is_file()