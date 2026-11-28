"""成片自检：时长、流参数、空帧、音频完整性。

用法: python -I verify.py <video.mp4> <ffmpeg.exe> <work_dir>
（ffmpeg-static 不带 ffprobe，所以这里是解析 ffmpeg -i 的 stderr）
"""
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

video, ffmpeg, work = sys.argv[1:4]
EXPECT = float(sys.argv[4]) if len(sys.argv) > 4 else 236.0
os.makedirs(work, exist_ok=True)
FAIL = []


def run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


# ---- 1. 容器与流参数 ----
banner = run([ffmpeg, "-hide_banner", "-i", video]).stderr
print(banner.strip().splitlines()[-6] if banner else "(no probe output)")

m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", banner)
dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0

vm = re.search(r"Stream #0:0.*?: Video: (\w+).*?(\d{3,4})x(\d{3,4}).*?(\d+(?:\.\d+)?) fps", banner)
w = h = 0
vcodec = ""
if vm:
    vcodec, w, h = vm.group(1), int(vm.group(2)), int(vm.group(3))
pix = re.search(r"yuv\d+p\(([^)]+)\)", banner)
fps_m = re.search(r"(\d+(?:\.\d+)?) fps", banner)
acodec = re.search(r"Audio: (\w+)", banner)
sr_m = re.search(r"Audio:.*?(\d{4,6}) Hz", banner)
ch_m = re.search(r"Audio:.*?, (stereo|mono)", banner)

print(f"duration : {dur:.2f}s  ({int(dur // 60)}:{int(dur % 60):02d})")
print(f"video    : {vcodec} {w}x{h} {fps_m.group(1) if fps_m else '?'}fps range/color={pix.group(1) if pix else '?'}")
print(f"audio    : {acodec.group(1) if acodec else 'none'} {sr_m.group(1) if sr_m else '?'}Hz {ch_m.group(1) if ch_m else '?'}")

if abs(dur - EXPECT) > 0.5:
    FAIL.append(f"duration {dur:.2f} != {EXPECT}")
if (w, h) != (1920, 1080):
    FAIL.append("not 1920x1080")
if not pix or "bt709" not in pix.group(1):
    FAIL.append(f"color tag is {pix.group(1) if pix else '?'} not bt709")
if not acodec:
    FAIL.append("no audio stream")

# ---- 2. 抽样帧：不应有空白/纯色帧 ----
samples = [int(dur * p) for p in (0.02, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.985)]
stats = []
for i, sec in enumerate(samples):
    out = os.path.join(work, f"chk_{i}.png")
    r = run([ffmpeg, "-y", "-ss", str(sec), "-i", video, "-frames:v", "1", out])
    if not os.path.exists(out):
        FAIL.append(f"cannot extract frame at {sec}s")
        continue
    arr = np.asarray(Image.open(out).convert("L")).astype(float)
    std = arr.std()
    stats.append((sec, round(std, 1)))
    if std < 2.0:
        FAIL.append(f"frame at {sec}s looks blank (std={std:.2f})")
print("frame std:", ", ".join(f"{s}s={v2}" for s, v2 in stats))

# ---- 3. 音频：无静音段、不削波 ----
wav = os.path.join(work, "audit.wav")
run([ffmpeg, "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav])
raw = run([ffmpeg, "-v", "error", "-i", wav, "-f", "f32le", "-"])
import struct
pcm = np.frombuffer(open(wav, "rb").read()[44:], dtype="<i2").astype(np.float64) / 32768
if pcm.size == 0:
    FAIL.append("audio decode empty")
else:
    sr = 16000
    win = sr * 5
    levels = [
        round(20 * np.log10(np.sqrt(np.mean(pcm[i:i + win] ** 2)) + 1e-9), 1)
        for i in range(0, len(pcm) - win, win)
    ]
    print(f"audio rms : min={min(levels)} dB  max={max(levels)} dB  peak={np.abs(pcm).max():.3f}")
    if max(levels) < -40:
        FAIL.append("audio effectively silent")
    if min(levels) < -45:
        FAIL.append(f"silent gap detected (min {min(levels)} dB)")
    if np.abs(pcm).max() > 0.995:
        FAIL.append("audio clipping")

print()
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")