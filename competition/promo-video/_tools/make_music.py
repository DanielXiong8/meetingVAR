"""为 meetingVAR 宣传片合成原创配乐（无人声）。

做法：纯加法合成。pad 铺底 + 拨弦琶音 + 低音 + 极轻空气噪声，
和弦在章节切换处换，所以每章的"和声颜色"和画面同步。
输出 48kHz 立体声 WAV。

用法: python -I make_music.py <out.wav>
"""
import sys
import wave

import numpy as np

SR = 48000
# 可选：python make_music.py <out.wav> [duration] [chapter_starts_csv]
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 236.0
N = int(SR * DUR)
rng = np.random.default_rng(20261125)

# 章节起点（与 promo.js 时间轴一致）
CHAPTERS = (
    [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3
    else [0.0, 35.4, 72.6, 108.6, 143.6, 173.6, 195.6, 217.6, DUR]
)

BPM = 84.0
BEAT = 60.0 / BPM
BAR = BEAT * 4


def hz(semi, base=440.0):
    """以 A4 为基准的半音偏移。"""
    return base * (2.0 ** (semi / 12.0))


# 和弦（以 MIDI 音高写，60 = C4）
CHORDS = {
    "Fmaj7": [53, 57, 60, 64],
    "Am7": [45, 48, 52, 55],
    "Dm7": [50, 53, 57, 60],
    "Bb6": [46, 50, 53, 55],
    "Gm7": [43, 46, 50, 53],
    "C7": [48, 52, 55, 58],
    "F6": [53, 57, 60, 62],
}

# 每章的和声走向（一章可多段，按章内时间均分）
PLAN = [
    ["Fmaj7"],
    ["Am7", "Dm7"],
    ["Bb6", "Fmaj7"],
    ["Gm7", "C7"],
    ["Dm7", "Bb6"],
    ["Fmaj7", "Am7"],
    ["Bb6", "C7"],
    ["F6"],
]

midi_hz = lambda m: 440.0 * (2.0 ** ((m - 69) / 12.0))

L = np.zeros(N, dtype=np.float64)
R = np.zeros(N, dtype=np.float64)


def add(buf_l, buf_r, start, sig, pan=0.0):
    """把 sig 叠加到 start 秒处，pan -1..1。"""
    i0 = int(start * SR)
    if i0 >= N:
        return
    sig = sig[: N - i0]
    gl = np.sqrt((1 - pan) / 2)
    gr = np.sqrt((1 + pan) / 2)
    buf_l[i0 : i0 + len(sig)] += sig * gl
    buf_r[i0 : i0 + len(sig)] += sig * gr


def pad_note(freq, dur, amp=0.16):
    n = int(dur * SR)
    t = np.arange(n) / SR
    # 慢起慢落，避免咔哒
    atk = np.minimum(1.0, t / 1.6)
    rel = np.minimum(1.0, (dur - t) / 2.2).clip(0, 1)
    env = atk * rel
    # 轻微颤音 + 三次谐波，模拟温暖的合成器 pad
    vib = 1.0 + 0.0016 * np.sin(2 * np.pi * 0.17 * t + rng.uniform(0, 6.28))
    s = np.sin(2 * np.pi * freq * t * vib)
    s += 0.22 * np.sin(2 * np.pi * freq * 2 * t)
    s += 0.07 * np.sin(2 * np.pi * freq * 3 * t)
    s += 0.03 * np.sin(2 * np.pi * freq * 4.01 * t)
    return amp * env * s


def pluck(freq, amp=0.09, dur=1.1):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t * 4.2) * np.minimum(1.0, t / 0.006)
    s = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
    s += 0.06 * np.sin(2 * np.pi * freq * 3 * t)
    return amp * env * s


def sub(freq, dur, amp=0.13):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.minimum(1.0, t / 0.8) * np.minimum(1.0, (dur - t) / 1.4).clip(0, 1)
    return amp * env * np.sin(2 * np.pi * freq * t)


def whoosh(dur=0.9, amp=0.05):
    """章节切点处的柔和高频扫过。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    # 一阶低通，截止频率随时间上升
    out = np.empty(n)
    y = 0.0
    coef = 0.004 + 0.02 * (t / dur)
    for i in range(n):
        y += coef[i] * (noise[i] - y)
        out[i] = y
    env = np.sin(np.pi * t / dur) ** 2
    return amp * env * out


# ---- pad：每段和弦铺满，左右微失谐 ----
# 章节数可以多于 PLAN 的和声段数，按需循环取用
for ci in range(len(CHAPTERS) - 1):
    chord_names = PLAN[ci % len(PLAN)]
    start = CHAPTERS[ci]
    end = CHAPTERS[ci + 1]
    seg = end - start
    per = seg / len(chord_names)
    for k, name in enumerate(chord_names):
        s0 = start + k * per
        for vi, midi in enumerate(CHORDS[name]):
            f = midi_hz(midi)
            jitter = rng.uniform(-0.7, 0.7)
            add(L, R, s0, pad_note(f * (1 + jitter * 1e-4), per, 0.13), pan=-0.30)
            add(L, R, s0, pad_note(f * (1 - jitter * 1e-4), per, 0.13), pan=0.30)
            # 八度加一层，厚度来源
            if vi == 0:
                add(L, R, s0, pad_note(f * 2, per, 0.05), pan=0.0)

# ---- 低音：每小节根音 ----
tpos = 0.0
while tpos < DUR - 0.5:
    ci = max(i for i in range(len(CHAPTERS) - 1) if CHAPTERS[i] <= tpos)
    name = PLAN[ci % len(PLAN)][0]
    root = CHORDS[name][0] - 12
    add(L, R, tpos, sub(midi_hz(root), BAR * 0.94, 0.11), pan=0.0)
    tpos += BAR * 2

# ---- 拨弦：稀疏的琶音，落在副拍上 ----
beat_i = 0
pattern = [0, 2, 1, 3, 2, None, 1, None]
while True:
    tpos = beat_i * BEAT
    if tpos >= DUR - 1.0:
        break
    ci = max(i for i in range(len(CHAPTERS) - 1) if CHAPTERS[i] <= tpos)
    chord_names = PLAN[ci % len(PLAN)]
    seg = CHAPTERS[ci + 1] - CHAPTERS[ci]
    k = min(len(chord_names) - 1, int((tpos - CHAPTERS[ci]) / (seg / len(chord_names))))
    notes = CHORDS[chord_names[k]]
    idx = pattern[beat_i % len(pattern)]
    if idx is not None:
        f = midi_hz(notes[idx] + 12)
        amp = 0.055 if beat_i % 4 else 0.075
        add(L, R, tpos, pluck(f, amp), pan=(-0.25 if beat_i % 2 else 0.25))
    beat_i += 1

# ---- 空气层：极轻的随机行走噪声 ----
air = rng.standard_normal(N) * 0.006
smooth = np.cumsum(air)
smooth -= np.linspace(smooth[0], smooth[-1], N)
L += smooth * 0.02
R += np.roll(smooth, 997) * 0.02

# ---- 章节切点：低频轻击 + 柔和高频 ----
for c in CHAPTERS[1:-1]:
    tt = np.arange(int(0.9 * SR)) / SR
    thump = 0.085 * np.exp(-tt * 7.0) * np.sin(2 * np.pi * 58 * tt)
    add(L, R, c, thump, pan=0.0)
    add(L, R, c, whoosh(0.75, 0.028), pan=0.0)

# ---- 收尾：淡入淡出 ----
fade_in = int(1.8 * SR)
fade_out = int(4.0 * SR)
env = np.ones(N)
env[:fade_in] = np.linspace(0, 1, fade_in) ** 1.5
env[-fade_out:] = np.linspace(1, 0, fade_out) ** 1.2
L *= env
R *= env

# ---- 归一化 ----
peak = max(np.abs(L).max(), np.abs(R).max())
target = 0.72
gain = target / peak if peak > 0 else 1.0
L *= gain
R *= gain

# ---- 去直流 ----
L -= L.mean()
R -= R.mean()

# ---- 慢速响度匹配：把 -12/-19 dB 的起伏拉平到统一底噪 ----
TARGET_RMS = 10 ** (-17.0 / 20.0)
mono = (L + R) / 2
win = int(0.5 * SR)
cs = np.cumsum(mono ** 2)
env = np.empty(N)
env[:win] = cs[:win] / np.arange(1, win + 1)
env[win:] = (cs[win:] - cs[:-win]) / win
env = np.sqrt(np.maximum(env, 1e-12))

g = np.clip((TARGET_RMS / env) ** 0.65, 0.55, 2.2)
# 平滑增益，避免抽吸（前缀和移动平均，避免 np.convolve 的 O(n·win)）
cg = np.cumsum(g)
gs = np.empty(N)
gs[:win] = cg[:win] / np.arange(1, win + 1)
gs[win:] = (cg[win:] - cg[:-win]) / win
g = gs
L *= g
R *= g

# ---- 软限幅，保住峰值余量 ----
peak = max(np.abs(L).max(), np.abs(R).max())
if peak > 0.89:
    L *= 0.89 / peak
    R *= 0.89 / peak

stereo = np.stack([L, R], axis=1)
pcm = np.clip(stereo * 32767.0, -32768, 32767).astype("<i2")

out = sys.argv[1] if len(sys.argv) > 1 else "music.wav"
with wave.open(out, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())

rms = np.sqrt(np.mean((stereo ** 2)))
print(f"wrote {out}  {DUR:.1f}s  peak={peak*gain:.3f}  rms={rms:.4f} ({20*np.log10(rms):.1f} dBFS)")