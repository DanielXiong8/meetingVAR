"""meetingVAR 全局配置：路径、模型、采集参数。

约定：模型权重在 D:/env/models（见 SETUP.md），运行期产物在 data/（gitignore）。
"""
from __future__ import annotations

import os
from pathlib import Path
from .env import load_local_env

load_local_env()

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
RING_SECONDS = 600        # 音频环形缓冲保留时长（≈10min，1s/格）
                          # 不变量：RING_SECONDS ≥ LOOKBACK_SECONDS（否则回溯被静默截短）

# 转写句最短长度：低于该长度的片段不入转写/监听/证据流。
# 默认 2 剔除单字噪声碎片；调成 1 可保留"是/好/停"类单字应答（代价是噪声也进来）。
MIN_TEXT_CHARS = int(__import__("os").environ.get("MEETINGVAR_MIN_TEXT_CHARS", "2"))

# ---- 录屏 ----
SCREEN_FPS = 2.0
SCREEN_BUFFER_FRAMES = 240  # 2fps × 120s 回溯

# ---- 高清补录（命中后 30fps 段）资源约束 ----
# 帧先双线性缩到 HD_WIDTH 宽再落盘：临时空间与合成耗时降 ~4 倍（1440p→1280 宽）
HD_WIDTH = int(__import__("os").environ.get("MEETINGVAR_HD_WIDTH", "1280"))
# 每场会议（一个进程）高清补录**临时帧序列**的总预算上限，所有命中共享；
# 预算折算帧率 = 预算/帧尺寸/补录时长（标称 30fps 只是上限，不承诺）
HD_TEMP_BUDGET_BYTES = int(float(__import__("os").environ.get("MEETINGVAR_HD_TEMP_GB", "5")) * 1024**3)

# ---- 触发 ----
LOOKBACK_SECONDS = int(__import__("os").environ.get("MEETINGVAR_LOOKBACK", "300"))  # 命中回溯窗（秒），可 env 调

HOTWORD_DEBOUNCE_SECONDS = 300  # 同一热词 5 分钟内不重复触发
L2_SIM_THRESHOLD = 0.25         # TF-IDF 兴趣相似度触发阈值（v0 拍脑袋值，用反馈校准）

# ---- 说话人（会话级在线声纹聚类）----
# 句级 CAM++ 声纹的余弦相似度阈值：≥ 归入既有说话人，不够像且句子够长 → 新说话人。
# v0 在 90s 换人样本上校准（test2 会议 14:00 起，换人点 ~34s，参照讯飞 LFASR 分人）：
# 安全窗口 0.40–0.60（异人 cos 最大 0.348；≥0.65 开始把同一人拆多号），取中值 0.50。
# 真实会议数据积累后再调。env 可覆盖。
SPEAKER_SIM_THRESHOLD = float(__import__("os").environ.get("MEETINGVAR_SPEAKER_SIM", "0.50"))

# ---- 路径 ----
DATA_DIR = Path("data")
POOL_DIR = DATA_DIR / "pool"
CLIPS_DIR = DATA_DIR / "clips"
TERMS_FILE = DATA_DIR / "terms.txt"  # 运行期术语表（可由项目记忆生成）

# ---- LLM（厚纪要可选；离线跑时留空则只出模板版并标注）----
LLM_BASE_URL = os.environ.get("MEETINGVAR_LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("MEETINGVAR_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("MEETINGVAR_LLM_MODEL", "")
LLM_MAX_TOKENS = os.environ.get("MEETINGVAR_LLM_MAX_TOKENS", "2048")
# Provider-specific extension: leave empty for generic/local compatible servers.
LLM_THINKING = os.environ.get("MEETINGVAR_LLM_THINKING", "").strip().lower()


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
