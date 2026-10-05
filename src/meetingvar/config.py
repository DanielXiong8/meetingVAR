"""meetingVAR 全局配置：路径、模型、采集参数。

约定：模型权重在 D:/env/models（见 SETUP.md），运行期产物在 data/（gitignore）。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- 模型 ----
MODELS_ROOT = Path(os.environ.get("MEETINGVAR_MODELS", "D:/env/models"))
ASR_MODEL_KEYWORD = os.environ.get("MEETINGVAR_ASR", "SenseVoiceSmall")  # 可换 seaco_paraformer
VAD_KEYWORD = "fsmn_vad"
SPK_KEYWORD = "campplus"
PUNC_KEYWORD = "punc_ct-transformer"

# ---- 音频 ----
ASR_RATE = 16000          # ASR 统一 16k 单声道
CHUNK_SECONDS = int(__import__("os").environ.get("MEETINGVAR_CHUNK", "8"))   # 增量转写块长（流式化：8s 步长）
CHUNK_OVERLAP_SECONDS = 3   # 块间重叠，避免句子被截断
RING_SECONDS = 600        # 音频环形缓冲保留时长（≈10min，命中回溯用）

# ---- 录屏 ----
SCREEN_FPS = 2.0
SCREEN_BUFFER_FRAMES = 240  # 2fps × 120s 回溯

# ---- 触发 ----
LOOKBACK_SECONDS = int(__import__("os").environ.get("MEETINGVAR_LOOKBACK", "300"))  # 命中回溯窗（秒），可 env 调

HOTWORD_DEBOUNCE_SECONDS = 300  # 同一热词 5 分钟内不重复触发
L2_SIM_THRESHOLD = 0.25         # TF-IDF 兴趣相似度触发阈值（v0 拍脑袋值，用反馈校准）

# ---- 路径 ----
DATA_DIR = Path("data")
POOL_DIR = DATA_DIR / "pool"
CLIPS_DIR = DATA_DIR / "clips"
TERMS_FILE = DATA_DIR / "terms.txt"  # 运行期术语表（可由项目记忆生成）

# ---- LLM（厚纪要可选；离线跑时留空则只出模板版并标注）----
LLM_BASE_URL = os.environ.get("MEETINGVAR_LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("MEETINGVAR_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("MEETINGVAR_LLM_MODEL", "")


def find_model_dir(keyword: str) -> str:
    """在 MODELS_ROOT 下按关键词定位模型目录（认 config.yaml / configuration.json）。"""
    hits = [
        p.parent
        for p in list(MODELS_ROOT.rglob("config.yaml")) + list(MODELS_ROOT.rglob("configuration.json"))
        if keyword.lower() in str(p).lower()
    ]
    if not hits:
        raise FileNotFoundError(f"MODELS_ROOT({MODELS_ROOT}) 下没找到含 {keyword!r} 的模型")
    return str(hits[0])
