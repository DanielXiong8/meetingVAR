"""Run a two-session, model-free demo of review feedback changing L2 behavior.

Run from the repository root with ``python scripts/demo_feedback_loop.py``.
All generated clips and calibration files stay in a temporary directory.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.cli import main  # noqa: E402
from meetingvar.memory.calibration import load_threshold  # noqa: E402
from meetingvar.pool.store import Pool  # noqa: E402


def main_demo() -> None:
    with tempfile.TemporaryDirectory(prefix="meetingvar-feedback-demo-") as tmp:
        root = Path(tmp)
        config.DATA_DIR = root / "data"
        config.POOL_DIR = config.DATA_DIR / "pool"
        config.CLIPS_DIR = config.DATA_DIR / "clips"
        project = root / "sample-project"
        project.mkdir()

        first = Pool("meeting-1")
        clip_id = first.add_clip(
            "L2",
            "interest_score=0.34",
            b"RIFF-demo",
            [{"ts": 72.0, "speaker": "S1", "text": "讨论到的内容与项目无关。"}],
        )
        first.update_review(clip_id, "useless", "这次误命中，下次收紧")
        print("=== 第一场：标记误命中 ===")
        sys.argv = [
            "meetingvar", "review", "--session", "meeting-1", "--summary", "--project", str(project)
        ]
        main()

        next_threshold = load_threshold(project)
        print("=== 第二场：项目自动加载校准后的 L2 阈值 ===")
        print(f"默认阈值：{config.L2_SIM_THRESHOLD:.2f}")
        print(f"下一场阈值：{next_threshold:.2f}")
        print("模型、声卡和网络均未使用；样例数据将在进程退出后自动清理。")


if __name__ == "__main__":
    main_demo()
