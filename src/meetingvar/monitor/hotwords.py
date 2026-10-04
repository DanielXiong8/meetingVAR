"""三层监听：L1 热词规则 / L2 兴趣相似度（TF-IDF）/ L3 手动 VAR（外部置位）。

L1：新转写文本中命中词表即触发，同一词 5 分钟去抖；
L2：兴趣文档（项目记忆/用户侧重）TF-IDF 化，转写滑窗相似度超阈值即触发；
L3：主裁主动要回看——由 pipeline 暴露的手动事件置位。
"""
from __future__ import annotations

import time

from .. import config


class HotwordMonitor:
    def __init__(self, terms: list[str], debounce_seconds: int = config.HOTWORD_DEBOUNCE_SECONDS):
        self.terms = [t for t in terms if t]
        self.debounce = debounce_seconds
        self._last_fire: dict[str, float] = {}

    def check(self, text: str, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        low = text.lower()
        fired = []
        for t in self.terms:
            if t.lower() not in low:
                continue
            last = self._last_fire.get(t.lower(), -1e9)
            if now - last >= self.debounce:
                self._last_fire[t.lower()] = now
                fired.append(t)
        return fired


class InterestScorer:
    """L2：兴趣文档 TF-IDF 相似度。focus_docs = 用户侧重 + 项目记忆段落。"""

    def __init__(self, focus_docs: list[str], threshold: float | None = None):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.threshold = threshold
        self._vec = TfidfVectorizer()
        corpus = [d for d in focus_docs if d and d.strip()] or [" "]
        self._mat = self._vec.fit_transform(corpus)

    def score(self, text: str) -> float:
        import jieba

        tokens = " ".join(jieba.cut(text))
        q = self._vec.transform([tokens])
        sims = (q @ self._mat.T).toarray()[0]
        return float(sims.max()) if sims.size else 0.0

    def check(self, text: str) -> tuple[bool, float]:
        if self.threshold is None:
            return False, 0.0
        s = self.score(text)
        return s >= self.threshold, s


class ManualVAR:
    """L3：手动触发的线程安全开关（record 模式里绑回车，replay 可程序置位）。"""

    def __init__(self):
        self._flag = False

    def fire(self) -> None:
        self._flag = True

    def consume(self) -> bool:
        if self._flag:
            self._flag = False
            return True
        return False
