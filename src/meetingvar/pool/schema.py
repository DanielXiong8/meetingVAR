"""待审片段池数据契约（docs/pool_schema.md 的代码化）。

A（流水线）与 B（产品/智能层）的接口：字段变更须双方确认，
改这里必须同步改 docs/pool_schema.md。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal, Optional

Layer = Literal["L1", "L2", "L3"]  # L1 热词规则 / L2 系统一打分 / L3 手动热键
ReviewStatus = Literal["pending", "useful", "useless"]


@dataclass
class Trigger:
    layer: Layer
    evidence: str  # "热词:'测序污染'" / "score=0.87" / "manual_hotkey"
    explanation: str = ""


@dataclass
class AudioRef:
    path: str  # data/clips/<clip_id>.wav，转正时才落盘
    start_ts: float  # 会议内相对秒
    end_ts: float


@dataclass
class ScreenRef:
    video_path: Optional[str] = None
    keyframes: list[str] = field(default_factory=list)
    hd_clip_dir: Optional[str] = None  # 命中后 30fps 高清补录产物目录（hd_clip.mp4 所在）


@dataclass
class TranscriptLine:
    ts: float  # 会议内相对秒
    speaker: str  # CAM++ 说话人标签，如 "S2"
    text: str


@dataclass
class Review:
    status: ReviewStatus = "pending"
    note: Optional[str] = None


@dataclass
class Clip:
    session_id: str
    trigger: Trigger
    audio: AudioRef
    transcript: list[TranscriptLine] = field(default_factory=list)
    screen: ScreenRef = field(default_factory=ScreenRef)
    review: Review = field(default_factory=Review)
    clip_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(s: str) -> "Clip":
        d = json.loads(s)
        d["trigger"] = Trigger(**d["trigger"])
        d["audio"] = AudioRef(**d["audio"])
        d["screen"] = ScreenRef(**d["screen"])
        d["transcript"] = [TranscriptLine(**t) for t in d["transcript"]]
        d["review"] = Review(**d["review"])
        return Clip(**d)
