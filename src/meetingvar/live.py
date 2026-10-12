"""Shared live capture lifecycle for record and go."""
from __future__ import annotations

import threading
import time
from contextlib import ExitStack

from . import config


def record_session(args, session_id, terms, focus_docs, l2_threshold, on_session=None):
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
                          screen_capture=screen, echo=True, l2_threshold=l2_threshold)
        session.hd = hd
        if on_session:
            on_session(session)
        print("[rec] 会议监听开始。回车=手动 VAR，Ctrl+C 结束。")
        stop = threading.Event()

        def wait_enter():
            try:
                while not stop.is_set():
                    input()
                    if not stop.is_set():
                        session.manual.fire()
            except EOFError:
                pass

        threading.Thread(target=wait_enter, daemon=True).start()
        # AudioCapture already buffers while screen/model setup runs.
        last_ts = getattr(audio, "session_start", time.time()) - 1.0
        t0 = time.monotonic()
        normal_shutdown = False
        try:
            while args.duration <= 0 or time.monotonic() - t0 < args.duration:
                pcm, since = audio.ingest_since(last_ts)
                if pcm:
                    session.ingest_pcm(pcm, since)
                    last_ts = since + len(pcm) / 2 / config.ASR_RATE
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
        return summary
