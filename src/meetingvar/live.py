"""Shared live capture lifecycle for record and go."""
from __future__ import annotations

import os
import threading
import time
from contextlib import ExitStack

from . import config
from .events import emit, enabled as desktop_events


def record_session(args, session_id, terms, focus_docs, l2_threshold, on_session=None, speaker_book=None,
                   correction_terms=None, interest_context=""):
    from .capture.audio import AudioCapture
    from .capture.screen import ScreenCapture
    from .pipeline import Session

    if args.no_screen and args.hd:
        raise SystemExit("--no-screen 与 --hd 不能同时使用；关闭录屏时请移除 --hd")
    engine = getattr(args, "asr", "local")
    mode = getattr(args, "interest", "legacy")
    if mode == "api" and not (config.LLM_BASE_URL and config.LLM_MODEL):
        raise SystemExit("API兴趣判断未配置；请填写API地址和模型，或明确选择仅手动保存")
    emit("capture_state", phase="loading", text="正在打开音频和屏幕采集")
    with ExitStack() as resources:
        status_stop = threading.Event()
        stop = threading.Event()
        index = getattr(args, "audio_device", None)
        mic_index = getattr(args, "mic_device", None)
        source = getattr(args, "source", "loopback")
        if source == "both":
            from .capture.audio import DualCapture
            print("[rec] 打开音频采集（系统声音 + 麦克风）…")
            audio = DualCapture(loopback_index=index, mic_index=mic_index)
        elif source == "mic":
            print("[rec] 打开麦克风…")
            audio = AudioCapture(device_index=mic_index, mic=True)
        else:
            print("[rec] 打开系统音频环回…")
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
        if desktop_events():
            def report_capture():
                while not status_stop.is_set():
                    if screen is not None and hasattr(screen, "get_status"):
                        emit("screen_state", **screen.get_status())
                    status_stop.wait(1.0)
            reporter = threading.Thread(target=report_capture, daemon=True)
            reporter.start()
            def stop_reporter():
                status_stop.set()
                reporter.join(timeout=2)
            resources.callback(stop_reporter)
        hd = None
        if args.hd:
            from .capture.hd import HDCapture
            hd = HDCapture()
            resources.callback(hd.stop)
        cred = getattr(args, "xfyun_cred", None)
        if engine in {"xfyun", "xfyun_rtasr"}:
            from pathlib import Path
            if not cred or not Path(cred).exists():
                raise SystemExit("讯飞识别需要有效凭证路径：--xfyun-cred")
        if engine == "xfyun":
            print("[rec] ASR: 讯飞IAT分块听写（无声纹；不是RTASR）")
        elif engine == "xfyun_rtasr":
            print("[rec] ASR: 讯飞RTASR连续转写（临时字幕→定稿，不自动点名）")
        else:
            print("[rec] ASR: 本地FunASR分块识别")
        session = Session(session_id, terms=terms, focus_docs=focus_docs, device=args.device,
                          screen_capture=screen, echo=True, l2_threshold=l2_threshold,
                          speaker_book=speaker_book, asr_engine=engine, xfyun_cred=cred,
                          correction_terms=correction_terms, auto_triggers=mode == "legacy")
        session.hd = hd
        if on_session:
            on_session(session)
        stream = None
        if engine == "xfyun_rtasr":
            from .transcribe.xfyun_rtasr import XfyunRealtime
            stream = XfyunRealtime(cred)
            resources.callback(stream.stop)
            stream.start()
        judge = None
        if mode == "api":
            from .monitor.semantic import SemanticMonitor
            judge = SemanticMonitor(interest_context, focus=os.environ.get("MEETINGVAR_INTEREST_FOCUS", ""))
            resources.callback(judge.stop)
            emit("interest_state", state="ready", message=f"API兴趣判断已启用 · {config.LLM_MODEL} · 等待定稿发言")
        elif mode == "manual":
            emit("interest_state", state="manual", message="仅手动保存；专业词纠错不会自动命中")
        ask_speakers = not getattr(args, "no_ask_speakers", False) and stream is None
        print("[rec] 会议采集开始。回车或 :var=手动 VAR，:stop 结束。"
              + ("新说话人出现时会询问名字。" if ask_speakers else "不自动弹出说话人点名。"), flush=True)

        def wait_enter():
            try:
                while not stop.is_set():
                    line = input()
                    if stop.is_set():
                        break
                    if line.strip() in (":stop", ":quit"):
                        stop.set()
                        break
                    if line.strip() == ":var":
                        session.manual.fire()
                        continue
                    msg = session.submit_answer(line) if ask_speakers else None
                    if msg is None:
                        session.manual.fire()
                    else:
                        print(msg, flush=True)
            except EOFError:
                pass

        threading.Thread(target=wait_enter, daemon=True).start()

        def receive_stream(events):
            for event in events:
                kind = event.get("type")
                if kind == "status":
                    emit("asr_stream_state", state=event.get("state"), message=event.get("message", ""))
                    if event.get("state") == "ready":
                        emit("capture_state", phase="ready", text="讯飞RTASR已连接，字幕持续更新")
                    continue
                if kind not in {"partial", "final"}:
                    continue
                emit("partial_line", id=event["id"], text=event["text"] if kind == "partial" else "")
                if kind == "final":
                    origin = event["start_ts"]
                    session.accept_segments([{"id": event["id"], "start": 0,
                                              "end": max(0, event["end_ts"] - origin),
                                              "text": event["text"], "speaker": "spk?"}], origin)

        def receive_judgments(events):
            for event in events:
                if event.get("type") == "hit":
                    clip_id = session.capture_interest(event)
                    if clip_id:
                        emit("interest_state", state="saved", message="已保存：" + event["reason"])
                else:
                    emit("interest_state", state=event.get("state", "error"), message=event.get("message", ""))

        def ingest(pcm, since):
            if desktop_events():
                import audioop
                emit("audio_state", rms=round(audioop.rms(pcm, 2) / 32768, 4),
                     captured_until=since + len(pcm) / 2 / config.ASR_RATE)
            session.ingest_pcm(pcm, since)
            if stream is not None:
                stream.feed(pcm, since)

        last_ts = getattr(audio, "session_start", time.time()) - 1.0
        t0 = time.monotonic()
        normal_shutdown = False
        summary = None
        try:
            if desktop_events() and engine == "local":
                emit("capture_state", phase="loading", text="采集已打开，正在加载本地识别模型")
                session.asr._ensure_loaded()
            if stream is None and not stop.is_set():
                emit("capture_state", phase="ready", text="采集已打开，按音频块转写（非连续流式）")
            while (args.duration <= 0 or time.monotonic() - t0 < args.duration) and not stop.is_set():
                pcm, since = audio.ingest_since(last_ts)
                if pcm:
                    ingest(pcm, since)
                    last_ts = since + len(pcm) / 2 / config.ASR_RATE
                if stream is not None:
                    receive_stream(stream.poll())
                if judge is not None:
                    judge.submit(session.interest_turns, session._last_flush_ts or time.time())
                    receive_judgments(judge.poll())
                if ask_speakers:
                    prompt = session.next_question_prompt()
                    if prompt:
                        print("\n" + prompt, flush=True)
                if session.manual.consume():
                    session.trigger_manual()
                stop.wait(0.1 if stream is not None else 0.5)
            normal_shutdown = True
        except KeyboardInterrupt:
            normal_shutdown = True
        finally:
            stop.set()
            status_stop.set()
            emit("capture_state", phase="stopping", text="正在处理尾段、兴趣判断和保存证据")
            stop_audio()
            if normal_shutdown:
                try:
                    pcm, since = audio.ingest_since(last_ts)
                except KeyboardInterrupt:
                    pcm = b""
                if pcm:
                    ingest(pcm, since)
                if stream is not None:
                    receive_stream(stream.finish(timeout=10))
                    emit("partial_line", id="", text="", clear=True)
                else:
                    session._flush(force=True)
                if session.manual.consume():
                    session.trigger_manual()
                if judge is not None:
                    judge.submit(session.interest_turns, session._last_flush_ts or time.time(), force=True)
                    receive_judgments(judge.finish(timeout=35))
                summary = session.finish()
        if summary and summary.get("speakers"):
            print("[spk] 本场点名：" + "、".join(f"{k}→{v}" for k, v in summary["speakers"].items()))
        if summary:
            if desktop_events() and summary.get("clips"):
                if screen is not None:
                    screen.stop()
                from .capture.video import finalize_recordings
                finalize_recordings(session.pool)
            emit("meeting_summary", clips=summary.get("clips", 0), lines=summary.get("lines", 0),
                 saved=bool(summary.get("transcript_json")))
        return summary
