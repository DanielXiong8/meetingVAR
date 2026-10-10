"""测试替身与确定性音频工具（供 tests/test_*.py 复用；本文件不被 discover 收集）。

原则：替身只替换"需要模型/设备才能跑"的边界（ASR、屏幕），
不绕过被测逻辑本身——管线、监听、落盘纪律、缓冲语义都按真实代码执行。
"""
from __future__ import annotations

import array
import sys
import types
import wave
from pathlib import Path

# ---- ASR 替身 ----

class FakeASR:
    """可控 ASR：不加载任何模型，按脚本返回句段。

    per_call: 每次 flush 依次弹出的句段列表；用尽后返回 default。
    句段支持 dict 或 (start, end, speaker, text) 四元组。
    """

    def __init__(self, per_call: list | None = None, default: list | None = None):
        self.per_call = [list(x) for x in (per_call or [])]
        self.default = list(default or [])
        self.calls: list[int] = []  # 每次 flush 的 pcm 字节数（证明音频确实进了处理链路）

    def transcribe_pcm16k(self, pcm: bytes) -> list[dict]:
        self.calls.append(len(pcm))
        segs = self.per_call.pop(0) if self.per_call else list(self.default)
        out = []
        for s in segs:
            if isinstance(s, dict):
                out.append(dict(s))
            else:
                out.append({"start": s[0], "end": s[1], "speaker": s[2], "text": s[3]})
        return out


def install_fake_asr(pipeline_module, asr: FakeASR) -> None:
    """把 Session 用的 ASR 类替换为返回同一替身实例的工厂。"""
    pipeline_module.ASR = lambda device="cpu": asr  # type: ignore[assignment]


# ---- 确定性"标记音频"：把秒序号编码成直流电平，拼接后可精确解码 ----

MARKER_BASE_LEVEL = 500      # 第 k 秒的电平 = 500 + k（int16，上限可到 ~32000）
MARKER_SAMPLES_PER_HALF = 8000   # 解码粒度 0.5s


def marker_pcm(total_seconds: int, offset_seconds: int = 0) -> bytes:
    """生成 total_seconds 秒标记音频：第 k 秒整段为恒定电平 (BASE + k + offset)。

    直流电平在被 ring 以字节切片/拼接后保持不变，解码 = 逐块求均值 → 精确恢复秒序号，
    因此可以检出"重复拼接、缺口、越界、时间戳与音频长度不一致"。
    """
    buf = array.array("h")
    for k in range(offset_seconds, offset_seconds + total_seconds):
        level = MARKER_BASE_LEVEL + k
        assert 0 < level < 32000, f"秒序号 {k} 超出标记范围"
        buf.extend([level] * 16000)
    return buf.tobytes()


def decode_marker_halves(wav_path: Path) -> tuple[list[int], int]:
    """读回 wav（16k 单声道 16bit），按 0.5s 求均值 → (秒序号序列, 总采样数)。

    每 0.5s 必须落在同一个 1s 标记块内（块内电平恒定），否则该半块是跨块噪声，
    会被断言检查发现。
    """
    with wave.open(str(wav_path), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000, (
            "标记音频必须是 16k 单声道 16bit"
        )
        data = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(data)
    n = len(samples)
    halves: list[int] = []
    for i in range(0, n // MARKER_SAMPLES_PER_HALF * MARKER_SAMPLES_PER_HALF, MARKER_SAMPLES_PER_HALF):
        chunk = samples[i : i + MARKER_SAMPLES_PER_HALF]
        mean = sum(chunk) / len(chunk)
        halves.append(round(mean) - MARKER_BASE_LEVEL)
    return halves, n


def assert_contiguous_levels(case, halves: list[int], first: int, last: int) -> None:
    """断言 halves = [first, first, first+1, first+1, ...] 直到 last（每 1s 占两个半块）。"""
    expected = []
    for k in range(first, last + 1):
        expected.extend([k, k])
    case.assertEqual(
        halves, expected,
        f"音频标记序列不连续（重复/缺口/越界）：实际 {halves[:8]}...{halves[-8:]}，期望 {first}..{last}",
    )


# ---- 屏幕替身（HD 补录状态机测试用，不接触真实显示器） ----

class FakeShot:
    """mss ScreenShot 的最小替身：rgb 为纯色渐变原始 RGB 缓冲。"""

    def __init__(self, width: int, height: int):
        self.size = (width, height)
        row = bytes(range(0, 256, max(1, 256 // (width * 3))))[: width * 3]
        self.rgb = (row * height)[: width * height * 3]


def install_fake_mss(width: int = 64, height: int = 48):
    """注入最小 mss 替身：monitors[1] + grab()，不抓真实屏幕。"""
    mod = types.ModuleType("mss")

    class _Sct:
        def __init__(self):
            self.monitors = [{"width": width, "height": height}, {"width": width, "height": height}]
            self.grabbed = 0

        def grab(self, mon):
            self.grabbed += 1
            return FakeShot(width, height)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    mod.mss = _Sct  # type: ignore[attr-defined]
    sys.modules["mss"] = mod
    return mod


def remove_fake_mss() -> None:
    sys.modules.pop("mss", None)
