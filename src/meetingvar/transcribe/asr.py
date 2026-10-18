"""ASR 封装：FunASR 模型只加载一次，对 16k 单声道 PCM 块做转写。

输出统一为 [{start, end, speaker, text, emb}]，时间相对块起点（秒）。
emb = 该句的 CAM++ 声纹向量（list[float]，取失败时 None）——
跨块的说话人身份由 pipeline 的会话级聚类（transcribe/speaker.py）用 emb 归并；
块内 spk 编号（FunASR 单次调用内部聚类，实测长会议会塌成全 spk0）只作降级兜底。
"""
from __future__ import annotations

import re

from .. import config

_TAG = re.compile(r"<\|[^|]*\|>")

_emb_warned = False  # 声纹失败只提示一次，不刷屏


def clean(text: str) -> str:
    return _TAG.sub("", text)


class ASR:
    """懒加载的 FunASR 管线（asr+vad+spk+punc）+ 独立声纹模型（句级 embedding）。"""

    def __init__(self, device: str = "cuda", with_embeddings: bool = True):
        self._device = device
        self.with_embeddings = with_embeddings  # 公开：pipeline 据此决定 emb 缺失时的降级策略
        self._model = None
        self._sv = None

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

    def _ensure_sv(self):
        """独立的 CAM++ 声纹模型：只做 embedding 抽取（不带聚类后端）。"""
        if self._sv is None:
            from funasr import AutoModel

            self._sv = AutoModel(
                model=config.find_model_dir(config.SPK_KEYWORD),
                device=self._device,
                disable_update=True,
                disable_pbar=True,
            )
        return self._sv

    def _embedding(self, waveform, start_ms: int, end_ms: int):
        """对句级音频片段取声纹向量；异常/过短片段降级 None（不拖垮转写主链路）。"""
        if not self.with_embeddings:
            return None
        lo = max(0, int(start_ms * config.ASR_RATE / 1000))
        hi = min(len(waveform), int(end_ms * config.ASR_RATE / 1000))
        seg = waveform[lo:hi]
        if len(seg) < config.ASR_RATE // 4:  # <0.25s：不给声纹下任何结论
            return None
        try:
            res = self._ensure_sv().generate(input=seg, fs=config.ASR_RATE)
            emb = res[0].get("spk_embedding") if res else None
            if emb is None:
                return None
            if hasattr(emb, "detach"):  # torch.Tensor → list[float]
                emb = emb.detach().cpu().numpy()
            return [float(x) for x in emb.reshape(-1).tolist()]
        except Exception as exc:  # noqa: BLE001 — 声纹是增强项，失败只降级
            global _emb_warned
            if not _emb_warned:
                _emb_warned = True
                print(f"[asr] 声纹提取失败，降级为块内编号（仅提示一次）: {type(exc).__name__}: {exc}")
            return None

    def transcribe_pcm16k(self, pcm: bytes) -> list[dict]:
        """输入 16kHz 单声道 16bit PCM，返回说话人标注句段（含句级声纹向量）。"""
        if not pcm:
            return []
        if len(pcm) % 2:
            raise ValueError("16-bit PCM 必须包含完整采样点")
        self._ensure_loaded()
        import numpy as np

        # FunASR accepts a normalized waveform directly. Writing and deleting
        # a temporary WAV is still persistence of unpromoted meeting audio.
        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        res = self._model.generate(input=waveform, fs=config.ASR_RATE, batch_size_s=60)
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
                        "emb": self._embedding(waveform, seg["start"], seg["end"]),
                    }
                )
        elif first.get("text"):
            out.append({
                "start": 0.0,
                "end": len(pcm) / 2 / config.ASR_RATE,
                "speaker": "spk?",
                "text": clean(first["text"]).strip(),
                "emb": None,
            })
        return [s for s in out if s["text"]]
