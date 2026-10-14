"""说话人名册：把用户命名的"名字"绑定到声纹质心，跨会话复用。

设计约束：
- 声纹属生物特征——只在**用户主动命名**时才登记（点名应答或后续命名动作），
  未命名的说话人绝不写入名册；文件存本地项目目录（<项目>/meetingvar/speakers.json）。
- 自动识别阈值（config.SPEAKER_BOOK_SIM）比场内聚类阈值更严：错认人名比
  多问一次的代价大——认不出就继续点名，绝不硬套名字。
- 名册损坏（手改/半写）不拖垮会议：load 时静默降级为空册。
- 同一名字再次登记时质心做跨会话滑动平均（tn+本报），声音随时间漂移也能跟上。

纯标准库实现（CI model-free 可用）。
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path


def _unit(vec) -> list[float]:
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm > 1e-6 else values


class SpeakerBook:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []  # {name, centroid, count, updated_at}
        self._dirty = False

    # ---- 读写 ----
    @classmethod
    def load(cls, path: Path | None) -> "SpeakerBook":
        book = cls(path)
        if book.path and book.path.exists():
            try:
                data = json.loads(book.path.read_text(encoding="utf-8"))
                for e in data.get("speakers", []):
                    if e.get("name") and e.get("centroid"):
                        book.entries.append({
                            "name": str(e["name"]),
                            "centroid": [float(x) for x in e["centroid"]],
                            "count": int(e.get("count", 1)),
                            "updated_at": float(e.get("updated_at", 0.0)),
                        })
            except (OSError, ValueError, TypeError):
                book.entries = []  # 名册损坏 → 空册继续（点名功能照常）
        return book

    def save(self) -> bool:
        if not (self.path and self._dirty):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"version": 1, "speakers": self.entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False
        return True

    # ---- 识别与登记 ----
    def match(self, centroid, threshold: float) -> tuple[str | None, float]:
        """返回 (最相似的名字或 None, 相似度)。低于阈值一律 None——认不出就问。"""
        if not self.entries or centroid is None:
            return None, -1.0
        c = _unit(centroid)
        best_name, best_sim = None, -1.0
        for e in self.entries:
            u = _unit(e["centroid"])
            sim = sum(a * b for a, b in zip(c, u))
            if sim > best_sim:
                best_name, best_sim = e["name"], sim
        return (best_name if best_sim >= threshold else None), best_sim

    def add(self, name: str, centroid) -> None:
        """登记/更新：同名做质心滑动平均（跨会话计数）。"""
        c = _unit(centroid)
        for e in self.entries:
            if e["name"] == name:
                n = e["count"]
                merged = [(x * n + y) / (n + 1) for x, y in zip(e["centroid"], c)]
                e["centroid"] = _unit(merged)
                e["count"] = n + 1
                e["updated_at"] = time.time()
                self._dirty = True
                return
        self.entries.append({"name": name, "centroid": c, "count": 1, "updated_at": time.time()})
        self._dirty = True
