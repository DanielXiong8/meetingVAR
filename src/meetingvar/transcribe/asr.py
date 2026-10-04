"""ASR 封装：FunASR 模型只加载一次，对 16k 单声道 PCM 块做转写。

输出统一为 [{start, end, speaker, text}]，时间相对块起点（秒）。
"""
from __future__ import annotations

import re
import tempfile
import wave
from pathlib import Path

from .. import config

_TAG = re.compile(r"<\|[^|]*\|>")


def clean(text: str) -> str:
    return _TAG.sub("", text)


class ASR:
    """懒加载的 FunASR 管线（asr+vad+spk+punc）。"""

    def __init__(self, device: str = "cuda"):
        self._device = device
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from funasr import AutoModel

        self._model = AutoModel(
            model=config.find_model_dir(config.ASR_MODEL_KEYWORD),
            vad_model=config.find_model_dir(config.VAD_KEYWORD),
            spk_model=config.find_model_dir(config.SPK_KEYWORD),
            punc_model=config.find_model_dir(config.PUNC_KEYWORD),
            device=self._device,
            disable_update=True,
            disable_pbar=True,
        )

    def transcribe_pcm16k(self, pcm: bytes) -> list[dict]:
        """输入 16kHz 单声道 16bit PCM，返回说话人标注句段。"""
        self._ensure_loaded()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            with wave.open(f.name, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(config.ASR_RATE)
                w.writeframes(pcm)
            tmp = f.name
        try:
            res = self._model.generate(input=tmp, batch_size_s=60)
        finally:
            Path(tmp).unlink(missing_ok=True)
        out: list[dict] = []
        first = res[0] if res else {}
        if "sentence_info" in first:  # 说话人级
            for seg in first["sentence_info"]:
                out.append(
                    {
                        "start": seg["start"] / 1000.0,
                        "end": seg["end"] / 1000.0,
                        "speaker": f"spk{seg.get('spk', '?')}",
                        "text": clean(seg["text"]).strip(),
                    }
                )
        elif first.get("text"):
            out.append({"start": 0.0, "end": len(pcm) / 2 / config.ASR_RATE, "speaker": "spk?", "text": clean(first["text"]).strip()})
        return [s for s in out if s["text"]]
