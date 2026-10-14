"""会话级在线声纹聚类：把每句的 CAM++ embedding 归并到全场稳定的说话人编号。

问题背景（2026-10-14 实测破案）：FunASR 管线返回的 spk 编号只在"单次 generate 调用"
内部聚类——本管线按 8s 块逐段调用，编号每块从 0 重来、短窗内聚类还会塌成单簇，
实测 test1 1548 句 / test2 902 句整场恒定 spk0（声纹模型本身没问题：整段 90s 一次调用
能把 ~34s 的换人点分得和讯飞参照一致）。

修复思路：跨块维护每个说话人的声纹质心（该人已入样 embedding 的滑动平均），
逐句做余弦相似度匹配，给全场统一编号 spk0/spk1/…（0 基，与历史产物格式一致）。

判定规则：
- 相似度 ≥ threshold → 归入该说话人，并把 embedding 并进质心；
- 不够像、但句子 ≥ MIN_NEW_SPEAKER_SECONDS → 新说话人；
- 不够像、且是短句（一两个字的应答声纹不稳）→ 按放宽幅度就近归附，绝不新建，
  且**不更新质心**（低置信的借用标签不得污染质心，见单测）；
  但放宽归附只允许命中"最近活跃"的说话人——短句（呃/对/然后这类填充词）
  只表示当前发言还在继续，不允许用它把发言权切回早已停下的另一个人；
  放宽后仍不够像 / 命中的是别人 → 标 spk?（宁标未知，不硬猜身份）。

纯标准库实现（余弦相似度手算）：CI 的 model-free 测试不依赖 numpy/funasr。
"""
from __future__ import annotations

import math

# 新说话人的最短声纹时长：更短的句子不作身份裁决（只准归附、不准新建）。
# 校准依据（90s 样本）：1.4s 的正常句被 1.5 门槛误伤，而 ≤0.4s 的碎片在 1.2 下依然被挡住。
MIN_NEW_SPEAKER_SECONDS = 1.2
SHORT_SEGMENT_RELAX = 0.10      # 短句就近归附的放宽幅度（绝不新建说话人）


def _normalize(vec) -> list[float]:
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm > 1e-6 else values


class OnlineSpeakerCluster:
    """在线说话人聚类：assign() 逐句调用，跨块维持身份。"""

    def __init__(self, threshold: float = 0.55):
        self.threshold = float(threshold)
        self._centroids: list[list[float]] = []  # 每人质心（已归一化）
        self._counts: list[int] = []             # 每人累计入样的句数（滑动平均权重）
        self._last_idx: int | None = None        # 最近活跃说话人（短句只允许归附他）

    @property
    def n_speakers(self) -> int:
        return len(self._centroids)

    def assign(self, emb, duration_s: float) -> str:
        """返回该句的全局说话人编号；emb 无效时返回 spk?。"""
        if emb is None:
            return "spk?"
        e = _normalize(emb)
        if not e:
            return "spk?"
        if not self._centroids:
            # 首句也要够时长才建人：一句"嗯"不能定义全场第一个人
            if duration_s < MIN_NEW_SPEAKER_SECONDS:
                return "spk?"
            self._add(e)
            return "spk0"
        sims = [sum(a * b for a, b in zip(e, c)) for c in self._centroids]
        best = max(range(len(sims)), key=sims.__getitem__)
        if sims[best] >= self.threshold:
            self._update(best, e)
            return f"spk{best}"
        if duration_s < MIN_NEW_SPEAKER_SECONDS:
            # 放宽归附：仅当命中的是最近活跃说话人（短句=当前发言的延续，
            # 不允许把发言权切回早已停下的别人）；短句声纹噪声大，不并进质心
            if sims[best] >= self.threshold - SHORT_SEGMENT_RELAX and best == self._last_idx:
                self._last_idx = best
                return f"spk{best}"
            return "spk?"
        self._add(e)
        return f"spk{len(self._centroids) - 1}"

    def _add(self, e: list[float]) -> None:
        self._centroids.append(list(e))
        self._counts.append(1)
        self._last_idx = len(self._centroids) - 1

    def _update(self, idx: int, e: list[float]) -> None:
        n = self._counts[idx]
        merged = [(c * n + x) / (n + 1) for c, x in zip(self._centroids[idx], e)]
        norm = math.sqrt(sum(v * v for v in merged))
        self._centroids[idx] = [v / norm for v in merged] if norm > 1e-6 else merged
        self._counts[idx] = n + 1
        self._last_idx = idx
