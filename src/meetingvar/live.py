"""Shared live capture lifecycle for record and go."""
from __future__ import annotations

import threading
import time
from contextlib import ExitStack

from . import config


def record_session(args, session_id, terms, focus_docs, l2_threshold, on_session=None, speaker_book=None):
    from .capture.audio import AudioCapture
    from .capture.screen import ScreenCapture
    from .pipeline import Session

    if args.no_screen and args.hd:
        raise SystemExit("--no-screen 与 --hd 不能同时使用；关闭录屏时请移除 --hd")
    with ExitStack() as resources:
        print("[rec] 打开系统音频环回…")
        index = getattr(args, "audio_device", None)
        audio = AudioCapture(device_index=index) if index is not None else AudioCapture()
        audio_stopped = False

        def stop_audio():
            nonlocal audio_stopped
            if not audio_stopped:
                audio_stopped = True
                audio.stop()

        resources.callback(stop_audio)
        print(f"[rec] 采集源: {audio.name}")
        screen = None
        if not args.no_screen:
            screen = ScreenCapture()
            resources.callback(screen.stop)
            print(f"[rec] 录屏缓冲: {screen.backend} @ {config.SCREEN_FPS}fps")
        hd = None
        if args.hd:
            from .capture.hd import HDCapture
            hd = HDCapture()
            resources.callback(hd.stop)
        session = Session(session_id, terms=terms, focus_docs=focus_docs, device=args.device,
                          screen_capture=screen, echo=True, l2_threshold=l2_threshold,
                          speaker_book=speaker_book)
        session.hd = hd
        if on_session:
            on_session(session)
        ask_speakers = not getattr(args, "no_ask_speakers", False)
        print("[rec] 会议监听开始。回车=手动 VAR，Ctrl+C 结束。"
              + ("新说话人出现时会询问'他是谁'。" if ask_speakers else ""))
        stop = threading.Event()

        def wait_enter():
            # 输入行归属：有点名待答 → 名字（空行=跳过）；否则回车=手动 VAR
            try:
                while not stop.is_set():
                    line = input()
                    if stop.is_set():
                        break
                    msg = session.submit_answer(line)
                    if msg is None:
                        session.manual.fire()
                    else:
                        print(msg, flush=True)
            except EOFError:
                pass

        threading.Thread(target=wait_enter, daemon=True).start()
        # AudioCapture already buffers while screen/model setup runs.
        last_ts = getattr(audio, "session_start", time.time()) - 1.0
        t0 = time.monotonic()
        normal_shutdown = False
        summary = None
        try:
            while args.duration <= 0 or time.monotonic() - t0 < args.duration:
                pcm, since = audio.ingest_since(last_ts)
                if pcm:
                    session.ingest_pcm(pcm, since)
                    last_ts = since + len(pcm) / 2 / config.ASR_RATE
                # 新说话人点名：在主循环打印提示（输入线程负责收答）
                if ask_speakers:
                    prompt = session.next_question_prompt()
                    if prompt:
                        print("\n" + prompt, flush=True)
                # A click during silence must not wait for the next ASR window.
                if session.manual.consume():
                    session.trigger_manual()
                stop.wait(0.5)
            normal_shutdown = True
        except KeyboardInterrupt:
            normal_shutdown = True
        finally:
            stop.set()
            # Freeze capture and consume the final buffered tail, including
            # samples that arrived between the last poll and Ctrl+C.
            stop_audio()
            # On a processing error, do not refeed already accepted samples or
            # mask the original exception with a second ASR/overlap failure.
            if normal_shutdown:
                try:
                    pcm, since = audio.ingest_since(last_ts)
                except KeyboardInterrupt:  # deterministic virtual-source EOF
                    pcm = b""
                if pcm:
                    session.ingest_pcm(pcm, since)
                if session.manual.consume():
                    session.trigger_manual()
                summary = session.finish()
        if summary and summary.get("speakers"):
            print("[spk] 本场点名：" + "、".join(f"{k}→{v}" for k, v in summary["speakers"].items()))
        if summary and session.pending_questions_count():
            print(f"[spk] 还有 {session.pending_questions_count()} 个说话人未命名（保持编号）")
        return summary
