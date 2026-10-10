"""待审片段池：按 pool_schema 落盘 Clip（JSON + 音频/截图媒体文件）。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .. import config
from .schema import AudioRef, Clip, ScreenRef, TranscriptLine, Trigger

REVIEW_STATUSES = {"pending", "useful", "useless"}


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
        audio_start_ts: float | None = None,
        audio_end_ts: float | None = None,
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
                start_ts=audio_start_ts if audio_start_ts is not None else (transcript_lines[0]["ts"] if transcript_lines else 0.0),
                end_ts=audio_end_ts if audio_end_ts is not None else (transcript_lines[-1]["ts"] if transcript_lines else 0.0),
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
            data = json.loads(f.read_text(encoding="utf-8"))
            # Session artifacts such as transcript.json and feedback.json are
            # not reviewable Clip records.
            if "trigger" in data and "clip_id" in data:
                out.append(data)
        return out

    def _clip_path(self, clip_id: str) -> Path:
        """Resolve an exact ID or an unambiguous short ID to a Clip JSON."""
        exact = self.dir / f"{clip_id}.json"
        if exact.exists():
            return exact
        matches = [p for p in self.dir.glob("*.json") if p.stem.startswith(clip_id)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise FileNotFoundError(f"片段不存在: {clip_id}")
        raise ValueError(f"片段前缀不唯一: {clip_id}")

    def update_review(self, clip_id: str, status: str, note: str | None = None) -> dict:
        """Persist feedback in the existing Clip.review contract."""
        if status not in REVIEW_STATUSES:
            raise ValueError(f"review.status 必须是 {sorted(REVIEW_STATUSES)}，收到: {status}")
        path = self._clip_path(clip_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "trigger" not in data or "clip_id" not in data:
            raise ValueError(f"不是可评审的 Clip JSON: {path.name}")
        data["review"] = {
            "status": status,
            "note": note.strip() if isinstance(note, str) and note.strip() else None,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return data

    def feedback_summary(self) -> dict:
        """Recompute an auditable session summary from Clip.review values."""
        clips = self.list_clips()
        by_status = {status: 0 for status in sorted(REVIEW_STATUSES)}
        by_layer: dict[str, dict[str, int]] = {}
        for clip in clips:
            status = clip.get("review", {}).get("status", "pending")
            if status not in by_status:
                status = "pending"
            by_status[status] += 1
            layer = clip.get("trigger", {}).get("layer", "unknown")
            layer_counts = by_layer.setdefault(layer, {s: 0 for s in sorted(REVIEW_STATUSES)})
            layer_counts[status] += 1
        return {
            "session_id": self.session_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(clips),
            "by_status": by_status,
            "by_layer": by_layer,
        }

    def write_feedback_summary(self) -> Path:
        path = self.dir / "feedback.json"
        path.write_text(
            json.dumps(self.feedback_summary(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path
