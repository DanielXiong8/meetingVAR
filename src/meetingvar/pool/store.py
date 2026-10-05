"""待审片段池：按 pool_schema 落盘 Clip（JSON + 音频/截图媒体文件）。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .. import config
from .schema import AudioRef, Clip, ScreenRef, TranscriptLine, Trigger


class Pool:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.dir = config.POOL_DIR / session_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def add_clip(
        self,
        trigger_layer: str,
        evidence: str,
        audio_wav: bytes,
        transcript_lines: list[dict],
        screens: list[tuple[float, bytes]] | None = None,
        explanation: str = "",
        hd_dir: str | None = None,
    ) -> str:
        clip_id = str(uuid.uuid4())
        clip_media = config.CLIPS_DIR / clip_id
        clip_media.mkdir(parents=True, exist_ok=True)

        wav_path = clip_media / f"{clip_id}.wav"
        wav_path.write_bytes(audio_wav)

        keyframes: list[str] = []
        if screens:
            from ..capture.screen import ScreenCapture

            frames_dir = clip_media / "screens"
            n = ScreenCapture.dump_frames(None, screens, frames_dir)
            if n:
                keyframes = [f"{clip_id}/screens/frame_{i:04d}.jpg" for i in range(n)]

        clip = Clip(
            clip_id=clip_id,
            session_id=self.session_id,
            trigger=Trigger(layer=trigger_layer, evidence=evidence, explanation=explanation),
            audio=AudioRef(
                path=str(wav_path).replace("\\", "/"),
                start_ts=transcript_lines[0]["ts"] if transcript_lines else 0.0,
                end_ts=transcript_lines[-1]["ts"] if transcript_lines else 0.0,
            ),
            screen=ScreenRef(video_path=None, keyframes=keyframes, hd_clip_dir=hd_dir),
            transcript=[
                TranscriptLine(ts=l["ts"], speaker=l["speaker"], text=l["text"]) for l in transcript_lines
            ],
        )
        (self.dir / f"{clip_id}.json").write_text(clip.to_json(), encoding="utf-8")
        return clip_id

    def list_clips(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            out.append(json.loads(f.read_text(encoding="utf-8")))
        return out
