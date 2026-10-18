"""Create a compact, synchronized video from a saved clip's frame timeline."""
from __future__ import annotations

import json
import math
import re
import subprocess
import wave
from pathlib import Path

from .hd import _ffmpeg
from ..events import emit


def frame_segments(frames, start, duration, blank="screens/unavailable.jpg"):
    end = start + duration
    current = blank
    points = []
    for ts, name in sorted(frames):
        if not math.isfinite(ts):
            continue
        if ts <= start:
            current = name
        elif ts < end:
            points.append((ts, name))
    segments = []
    pos = start
    for ts, name in points:
        if ts > pos:
            segments.append((current, ts - pos))
        current, pos = name, ts
    if end > pos:
        segments.append((current, end - pos))
    return segments


def encode_clip(clip_path: Path, ffmpeg=None) -> Path | None:
    clip = json.loads(clip_path.read_text(encoding="utf-8"))
    audio = Path(clip["audio"]["path"])
    media = audio.parent
    manifest = media / "screens" / "manifest.txt"
    if not manifest.exists():
        return None
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，已保留音频与屏幕序列，可在软件内同步回看")
    frames = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        name, ts = line.split("\t", 1)
        if re.fullmatch(r"frame_\d+\.jpg", name) and (media / "screens" / name).is_file():
            frames.append((float(ts), "screens/" + name))
    if not frames:
        return None
    with wave.open(str(audio), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
    if duration <= 0:
        return None
    from PIL import Image, ImageDraw
    with Image.open(media / frames[0][1]) as img:
        width = max(2, img.width // 2 * 2)
        height = max(2, img.height // 2 * 2)
    blank = media / "screens" / "unavailable.jpg"
    canvas = Image.new("RGB", (width, height), (23, 33, 45))
    ImageDraw.Draw(canvas).text((24, 24), "Screen unavailable for this interval", fill="white")
    canvas.save(blank, quality=75)
    segments = frame_segments(frames, float(clip["audio"]["start_ts"]), duration)
    listing = media / "screen_timeline.ffconcat"
    lines = ["ffconcat version 1.0"]
    for name, length in segments:
        lines += [f"file '{name}'", f"duration {length:.6f}"]
    lines.append(f"file '{segments[-1][0]}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = media / "recording.mp4"
    temporary = media / "recording.part.mp4"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "1",
           "-i", listing.name, "-i", audio.name,
           "-vf", f"fps=5,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "64k", "-t", f"{duration:.6f}", "-movflags", "+faststart", temporary.name]
    result = subprocess.run(cmd, cwd=str(media), capture_output=True, timeout=max(120, duration * 2), creationflags=flags)
    if result.returncode or not temporary.exists() or temporary.stat().st_size == 0:
        raise RuntimeError("录屏视频合成失败，原始证据保留：" + result.stderr[-400:].decode("utf-8", "replace"))
    verify = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(temporary), "-f", "null", "-"],
                            capture_output=True, timeout=max(60, duration), creationflags=flags)
    if verify.returncode:
        raise RuntimeError("录屏视频解码验证失败，原始证据保留")
    temporary.replace(output)
    clip["screen"]["video_path"] = str(output).replace("\\", "/")
    clip_path.write_text(json.dumps(clip, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def finalize_recordings(pool):
    for clip in pool.list_clips():
        if not (clip.get("screen") or {}).get("keyframes"):
            continue
        emit("capture_state", phase="stopping", text="正在合成带声音的轻量录屏，原始证据已保存")
        try:
            output = encode_clip(pool.dir / (clip["clip_id"] + ".json"))
            if output:
                print(f"[screen] 已保存同步录屏: {output}", flush=True)
        except Exception as exc:
            print(f"[screen] {exc}", flush=True)
            emit("recording_warning", text=str(exc))
