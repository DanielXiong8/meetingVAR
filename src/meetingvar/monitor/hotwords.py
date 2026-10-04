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
    """L2：兴趣文档 TF-IDF 相似度。

    语料和查询必须使用同一套中文分词，否则中文文档会被当成整段词，
    查询几乎无法命中；阈值默认读取全局配置，确保 L2 在 pipeline 中真的可用。
    """

    def __init__(self, focus_docs: list[str], threshold: float | None = None):
        import jieba
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.threshold = config.L2_SIM_THRESHOLD if threshold is None else threshold
        self._tokenize = lambda text: " ".join(jieba.cut(text))
        corpus = [self._tokenize(d) for d in focus_docs if d and d.strip()]
        self._vec = TfidfVectorizer()
        self._mat = self._vec.fit_transform(corpus) if corpus and any(corpus) else None

    def score(self, text: str) -> float:
        if not text.strip() or self._mat is None:
            return 0.0
        q = self._vec.transform([self._tokenize(text)])
        sims = (q @ self._mat.T).toarray()[0]
        return float(sims.max()) if sims.size else 0.0

    def check(self, text: str) -> tuple[bool, float]:
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
