"""meetingvar 命令行入口。

    meetingvar replay  --wav <file> [--terms data/terms.txt] [--memory <folder>] [--device cuda]
    meetingvar record  [--terms data/terms.txt] [--memory <folder>] [--no-screen] [--duration 600]
    meetingvar digest  --session <id> [--llm]
    meetingvar devices
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from . import config


def load_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def load_memory_docs(folder: Path) -> list[str]:
    docs = []
    for f in folder.rglob("*") if folder.exists() else []:
        if f.suffix.lower() in {".md", ".txt"}:
            docs.append(f.read_text(encoding="utf-8"))
    return docs


def resolve_terms(args) -> list[str]:
    """词表解析顺序：显式 --terms 文件 > 从 --memory 自动抽取（agent 自己读）> 空。"""
    if args.terms:
        return load_terms(Path(args.terms))
    if args.memory:
        from .memory.extract import extract_terms, write_terms_file

        terms = extract_terms(Path(args.memory))
        out = Path(config.TERMS_FILE)
        write_terms_file(terms, out)
        print(f"[terms] 未指定词表，已从项目记忆自动抽取 {len(terms)} 词 -> {out}")
        return list(terms)
    return []


def cmd_replay(args) -> None:
    from .replay import replay

    terms = resolve_terms(args)
    focus = load_memory_docs(Path(args.memory)) if args.memory else None
    session_id = f"replay_{int(time.time())}"
    replay(Path(args.wav), session_id, terms=terms, focus_docs=focus, device=args.device)
    print(f"[next] meetingvar digest --session {session_id}")


def cmd_record(args) -> None:
    from .capture.audio import AudioCapture
    from .capture.screen import ScreenCapture
    from .pipeline import Session

    terms = resolve_terms(args)
    focus = load_memory_docs(Path(args.memory)) if args.memory else None
    session_id = f"live_{int(time.time())}"

    print("[rec] 打开系统音频环回…")
    audio = AudioCapture()
    print(f"[rec] 采集源: {audio.name}")
    screen = None
    if not args.no_screen:
        screen = ScreenCapture()
        print(f"[rec] 录屏缓冲: {screen.backend} @ {config.SCREEN_FPS}fps")

    session = Session(
        session_id, terms=terms, focus_docs=focus, device=args.device, screen_capture=screen, echo=True
    )
    print("[rec] 开始。Ctrl+C 结束；**按回车 = 手动 VAR**")
    stop = threading.Event()

    def _wait_enter():
        try:
            while not stop.is_set():
                input()
                session.manual.fire()
        except EOFError:
            pass

    threading.Thread(target=_wait_enter, daemon=True).start()

    last_ts = time.time()
    t0 = time.time()
    try:
        while not stop.is_set() and (args.duration <= 0 or time.time() - t0 < args.duration):
            pcm, since = audio.ingest_since(last_ts)
            last_ts = time.time()
            if pcm:
                session.ingest_pcm(pcm, since)
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        summary = session.finish()
        audio.stop()
        if screen:
            screen.stop()
    print(f"[done] {summary['lines']} 句 / {summary['clips']} 片段 / 纠错 {len(summary['corrections'])} 处")
    print(f"[next] meetingvar digest --session {session_id}")


def cmd_digest(args) -> None:
    from .report.generate import digest

    digest(args.session, use_llm=args.llm)


def cmd_devices(args) -> None:
    from .capture.audio import AudioCapture

    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    for info in p.get_loopback_device_info_generator():
        print(f"  [{info['index']}] {info['name']} ({int(info['maxInputChannels'])}ch @ {int(info['defaultSampleRate'])}Hz)")


def cmd_terms(args) -> None:
    from .memory.extract import extract_terms, write_terms_file

    extra = load_terms(Path(args.extra)) if args.extra else []
    terms = extract_terms(Path(args.memory), extra_terms=extra)
    n = write_terms_file(terms, Path(args.out))
    print(f"[terms] {n} 个监听词（自动抽取 + 手动 {len(extra)}）-> {args.out}")
    for w, src in terms.items():
        print(f"  {w}  {'/'.join(src)}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="meetingvar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("replay", help="wav 回放走全管线")
    rp.add_argument("--wav", required=True)
    rp.add_argument("--terms", default=None, help="词表文件；缺省时从 --memory 自动抽取")
    rp.add_argument("--memory", default=None, help="项目记忆文件夹（L2 兴趣文档）")
    rp.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    rc = sub.add_parser("record", help="实时采集（系统声音）")
    rc.add_argument("--terms", default=None, help="词表文件；缺省时从 --memory 自动抽取")
    rc.add_argument("--memory", default=None)
    rc.add_argument("--no-screen", action="store_true")
    rc.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    rc.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    dg = sub.add_parser("digest", help="会后生成薄/厚纪要")
    dg.add_argument("--session", required=True)
    dg.add_argument("--llm", action="store_true", help="调用 LLM 生成叙述版（需环境变量）")

    sub.add_parser("devices", help="列出环回设备")

    tm = sub.add_parser("terms", help="F1：从项目记忆自动抽取监听词表（术语+人名）")
    tm.add_argument("--memory", required=True, help="项目记忆文件夹（md/txt）")
    tm.add_argument("--out", default=str(config.TERMS_FILE))
    tm.add_argument("--extra", default=None, help="手动热词文件（用户主动输入，永远保留）")

    args = ap.parse_args()
    {"replay": cmd_replay, "record": cmd_record, "digest": cmd_digest,
     "devices": cmd_devices, "terms": cmd_terms}[args.cmd](args)


if __name__ == "__main__":
    main()
