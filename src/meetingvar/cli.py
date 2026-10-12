"""meetingvar 命令行入口。

    meetingvar replay  --wav <file> [--terms data/terms.txt] [--memory <folder>] [--device cuda]
    meetingvar record  [--terms data/terms.txt] [--memory <folder>] [--no-screen] [--duration 600]
    meetingvar digest  --session <id> [--llm]
    meetingvar devices
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from . import config


def load_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for l in path.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        if l.startswith("#!keep"):
            out.append(l.split(maxsplit=1)[1].strip())  # 手动词（rescan 永久保留）
            continue
        out.append(l)
    return out


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
    replay(Path(args.wav), session_id, terms=terms, focus_docs=focus, device=args.device,
           correction_terms=_correction_terms_arg(args))
    print(f"[next] meetingvar digest --session {session_id}")


def _correction_terms_arg(args) -> list[str] | None:
    """--correction-terms：严进纠错表（治理后的子集）。缺省 None → 回落监听表。"""
    return load_terms(Path(args.correction_terms)) if getattr(args, "correction_terms", None) else None


def cmd_transcribe(args) -> None:
    """转写模式：全场转写显式保存为 txt（用户点名要的落盘；开会模式零落盘默认不变）。"""
    from .replay import replay

    terms = resolve_terms(args)
    focus = load_memory_docs(Path(args.memory)) if args.memory else None
    session_id = f"transcribe_{int(time.time())}"
    out = Path(args.out) if args.out else None
    if out is None:
        raise SystemExit("[transcribe] 必须给 --out <转写txt路径>（这是转写模式与 replay 的唯一区别）")
    summary = replay(
        Path(args.wav), session_id, terms=terms, focus_docs=focus, device=args.device, transcript_out=out,
        correction_terms=_correction_terms_arg(args),
    )
    print(f"[done] 转写 {summary['lines']} 句 / 纠错 {len(summary['corrections'])} 处 -> {out}")
    if summary["clips"]:
        print(f"[next] meetingvar digest --session {session_id}  （命中片段纪要）")


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

    hd = None
    if args.hd:
        from .capture.hd import HDCapture

        hd = HDCapture()
    session = Session(
        session_id, terms=terms, focus_docs=focus, device=args.device, screen_capture=screen, echo=True
    )
    session.hd = hd
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
    # 选词人模式（2026-10-12 C 路线定案）：--curate 时 LLM 直接产出 10~20 个核心关键词，
    # L0/L1 降级为参考资料，不再产出千级词表。
    if getattr(args, "curate", False):
        from .memory.curate import curate_terms
        from .memory.extract import write_terms_file

        r = curate_terms(Path(args.memory) if args.memory else None,
                         Path(args.repo) if args.repo else None)
        if r is None:
            raise SystemExit("[terms] 选词人模式失败：LLM 未配置（MEETINGVAR_LLM_*）或解析失败")
        out_p = Path(args.out)
        write_terms_file({k["term"]: ["选词人"] for k in r["keywords"]}, out_p)
        report = out_p.parent / f"{out_p.stem}.curate.json"
        report.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[terms] 选词人模式：{len(r['keywords'])} 个核心关键词（上限 {20}）-> {out_p}")
        for k in r["keywords"]:
            print(f"  {k['term']}  — {k['why']}")
        for p in r["persons"]:
            print(f"  人物: {p['name']} — {p.get('role', '?')}（{p.get('why', '')}）")
        print(f"[terms] 选词报告 -> {report}（termset_id={r['termset_id']}）")
        return

    from .memory.extract import extract_terms, llm_extract, write_terms_file

    extra = load_terms(Path(args.extra)) if args.extra else []
    terms = extract_terms(Path(args.memory), extra_terms=extra) if args.memory else {}
    persons = []
    # L0 工具层：--repo 给了就跑（代码仓库符号抽取，零 LLM，任意大小）
    if args.repo:
        from .memory.repo_extract import repo_terms

        rterms, candidates = repo_terms(Path(args.repo))
        for w, s in rterms.items():
            terms.setdefault(w, []).extend(s)
        print(f"[terms] 仓库工具层: {len(rterms)} 词, 关键文件候选 {len(candidates)} 个")
    # L2 仓库语义层：--api 且给了 repo → API 只读候选摘要
    if args.api and args.repo:
        from .memory.repo_extract import repo_semantic

        sterms, files = repo_semantic(candidates)
        for w, s in sterms.items():
            terms.setdefault(w, []).extend(s)
        for f in files:
            if isinstance(f, dict) and f.get("path") and f.get("function"):
                print(f"  功能: {f['path']} — {str(f['function'])[:60]}")
        print(f"[terms] 仓库语义层(API): +{len(sterms)} 词")
    if args.llm and args.memory:
        r = llm_extract(Path(args.memory))
        if r:
            llm_terms, persons = r
            for w, srcs in llm_terms.items():
                terms.setdefault(w, []).extend(srcs)
        else:
            print("[terms] LLM 未配置或解析失败，已回落 jieba 层")
    n = write_terms_file(terms, Path(args.out))
    print(f"[terms] {n} 个监听词（jieba 自动抽取 + 手动 {len(extra)} + LLM{' ON' if args.llm else ' OFF'}）-> {args.out}")
    if getattr(args, "govern", False):
        import json as _json

        from .memory.govern import govern_terms

        g = govern_terms(terms, manual=set(extra))
        out_p = Path(args.out)
        correction_path = out_p.parent / f"{out_p.stem}.correction.txt"
        report_path = out_p.parent / f"{out_p.stem}.govern.json"
        correction_path.write_text("\n".join(g.correction) + "\n", encoding="utf-8")
        report_path.write_text(
            _json.dumps([r.__dict__ for r in g.records], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        st = g.stats()
        print(f"[terms] 治理完成：监听 {len(g.watch)} / 可纠错 {len(g.correction)} / 状态分布 {st}")
        print(f"[terms] 纠错表 -> {correction_path}；治理报告 -> {report_path}")
    for w, src in terms.items():
        print(f"  {w}  {'/'.join(src)}")
    for p in persons:
        print(f"  人物: {p['name']} — {p.get('role', '?')}（{p.get('why', '')}）")


def main() -> None:
    from .go import cmd_go, register as register_go

    ap = argparse.ArgumentParser(prog="meetingvar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("replay", help="wav 回放走全管线")
    rp.add_argument("--wav", required=True)
    rp.add_argument("--terms", default=None, help="监听词表文件；缺省时从 --memory 自动抽取")
    rp.add_argument("--correction-terms", default=None, help="严进纠错词表（治理后子集）；缺省回落监听表")
    rp.add_argument("--memory", default=None, help="项目记忆文件夹（L2 兴趣文档）")
    rp.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    tr = sub.add_parser("transcribe", help="转写模式：跑全管线并把全场转写显式存为 txt（--out 必填）")
    tr.add_argument("--wav", required=True)
    tr.add_argument("--out", required=True, help="全场转写输出 txt（[时:分:秒] 说话人: 文本 每行一句）")
    tr.add_argument("--terms", default=None, help="监听词表文件；缺省时从 --memory 自动抽取")
    tr.add_argument("--correction-terms", default=None, help="严进纠错词表（治理后子集）；缺省回落监听表")
    tr.add_argument("--memory", default=None, help="项目记忆文件夹（L2 兴趣文档）")
    tr.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    rc = sub.add_parser("record", help="实时采集（系统声音）")
    rc.add_argument("--terms", default=None, help="词表文件；缺省时从 --memory 自动抽取")
    rc.add_argument("--memory", default=None)
    rc.add_argument("--no-screen", action="store_true")
    rc.add_argument("--hd", action="store_true", help="命中后高清补录 ≤90s（帧缩至 1280 宽；帧率按临时预算折算，实测约 13-20fps）")
    rc.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    rc.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    dg = sub.add_parser("digest", help="会后生成薄/厚纪要")
    dg.add_argument("--session", required=True)
    dg.add_argument("--llm", action="store_true", help="调用 LLM 生成叙述版（需环境变量）")

    sub.add_parser("devices", help="列出环回设备")

    tm = sub.add_parser("terms", help="F1：从项目记忆自动抽取监听词表（术语+人名）")
    tm.add_argument("--memory", default=None, help="项目记忆文件夹（md/txt），文档层输入")
    tm.add_argument("--out", default=str(config.TERMS_FILE))
    tm.add_argument("--extra", default=None, help="手动热词文件（用户主动输入，永远保留）")
    tm.add_argument("--llm", action="store_true", help="文档语义层：本地小模型抽人名/角色/焦点（需 MEETINGVAR_LLM_*）")
    tm.add_argument("--repo", default=None, help="代码仓库路径（L0 工具层：符号抽取，零 LLM）")
    tm.add_argument("--govern", action="store_true", help="治理：监听表/纠错表分离（词形+常用词守卫），另写 <out>.correction.txt 与 <out>.govern.json")
    tm.add_argument("--curate", action="store_true", help="选词人模式（推荐）：LLM 直接产出 10~20 个核心关键词（带理由），L0/L1 仅作参考资料")
    tm.add_argument("--api", action="store_true", help="仓库语义层：API 大模型只读关键文件候选摘要（需 MEETINGVAR_LLM_* 指向 API）")

    rs = sub.add_parser("rescan", help="把项目内全部归档纪要+文档重扫一遍，更新该项目热词库")
    rs.add_argument("project", help="项目根目录")
    rs.add_argument("--extra", default=None, help="手动词文件（#!keep 永久保留）")
    rs.add_argument("--llm", action="store_true", help="叠加本地/云端小模型语义抽取")

    register_go(sub)

    args = ap.parse_args()
    {"replay": cmd_replay, "transcribe": cmd_transcribe, "record": cmd_record, "digest": cmd_digest,
     "devices": cmd_devices, "terms": cmd_terms, "go": cmd_go, "rescan": cmd_rescan}[args.cmd](args)


if __name__ == "__main__":
    main()


def cmd_rescan(args) -> None:
    from .memory.project_store import rescan_terms

    extra = load_terms(Path(args.extra)) if args.extra else []
    words, n_minutes = rescan_terms(Path(args.project), extra_terms=extra, use_llm=args.llm)
    print(f"[rescan] 扫描归档纪要 {n_minutes} 份 + 项目文档 -> 热词库 {len(words)} 词")
    print(f"[rescan] 位置: {Path(args.project) / 'meetingvar' / 'terms.txt'}")
