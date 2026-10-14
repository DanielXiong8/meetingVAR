"""meetingvar 命令行入口。

    meetingvar replay  --wav <file> [--terms data/terms.txt] [--memory <folder>] [--device cuda]
    meetingvar record  [--terms data/terms.txt] [--memory <folder>] [--no-screen] [--duration 600]
    meetingvar digest  --session <id> [--llm]
    meetingvar review  --session <id> [--clip <id>] [--status useful|useless]
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
    out = []
    for l in path.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if not l:
            continue
        if l.startswith("#!keep"):
            parts = l.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                out.append(parts[1].strip())
            continue
        if l.startswith("#"):
            continue
        out.append(l)
    return out


def load_memory_docs(folder: Path) -> list[str]:
    from .memory.files import project_files

    docs = []
    for f in project_files(folder, {".md", ".txt"}):
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
    from .memory.calibration import load_threshold

    terms = resolve_terms(args)
    focus = load_memory_docs(Path(args.memory)) if args.memory else None
    threshold = load_threshold(Path(args.memory)) if args.memory else None
    if threshold is not None:
        print(f"[calibration] 项目 L2 阈值: {threshold:.2f}")
    session_id = f"replay_{time.time_ns()}"
    replay(Path(args.wav), session_id, terms=terms, focus_docs=focus, device=args.device,
           correction_terms=_correction_terms_arg(args), l2_threshold=threshold)
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
    from .live import record_session
    from .memory.calibration import load_threshold

    terms = resolve_terms(args)
    focus = load_memory_docs(Path(args.memory)) if args.memory else None
    session_id = f"live_{time.time_ns()}"
    threshold = load_threshold(Path(args.memory)) if args.memory else None
    if threshold is not None:
        print(f"[calibration] 项目 L2 阈值: {threshold:.2f}")
    summary = record_session(args, session_id, terms, focus, threshold)
    print(f"[done] session={session_id} / {summary['lines']} 句 / {summary['clips']} 片段 / 纠错 {len(summary['corrections'])} 处")
    if summary.get("transcript_json"):
        print(f"[next] meetingvar digest --session {session_id}")
    else:
        print("[rec] 本场零命中：草稿转写已丢弃，未保存会议记录")


def cmd_digest(args) -> None:
    from .report.generate import digest

    digest(args.session, use_llm=args.llm)


def cmd_archive(args) -> None:
    from .memory.project_store import archive_report

    path = archive_report(Path(args.project), args.session)
    print(f"[archive] {path}")


def _review_time(ts: float) -> str:
    total = max(0, int(float(ts)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _print_review_clip(clip: dict, index: int | None = None) -> None:
    prefix = f"[{index}] " if index is not None else ""
    review = clip.get("review", {})
    audio = clip.get("audio", {})
    trigger = clip.get("trigger", {})
    screen = clip.get("screen", {})
    print(
        f"{prefix}{clip['clip_id'][:8]}  "
        f"[{_review_time(audio.get('start_ts', 0))}–{_review_time(audio.get('end_ts', 0))}]  "
        f"{trigger.get('layer', '?')} / {trigger.get('evidence', '')}  "
        f"评价={review.get('status', 'pending')}"
    )
    lines = clip.get("transcript", [])
    if lines:
        excerpt = " / ".join(f"{x.get('speaker', '?')}: {x.get('text', '')}" for x in lines[:3])
        print(f"    {excerpt[:240]}")
    media = len(screen.get("keyframes", []))
    if media:
        print(f"    屏幕关键帧: {media} 张；音频: {audio.get('path', '')}")
    if review.get("note"):
        print(f"    备注: {review['note']}")


def _review_summary(pool) -> None:
    summary = pool.feedback_summary()
    counts = summary["by_status"]
    print(
        f"[feedback] {summary['session_id']}: 总计 {summary['total']} 片段；"
        f"待评 {counts['pending']} / 有用 {counts['useful']} / 没用 {counts['useless']}"
    )
    for layer, values in sorted(summary["by_layer"].items()):
        print(f"  {layer}: 待评 {values['pending']} / 有用 {values['useful']} / 没用 {values['useless']}")


def _calibrate_project(pool, project: str | None) -> None:
    if not project:
        return
    from .memory.calibration import apply_reviews

    result = apply_reviews(Path(project), pool.list_clips())
    print(
        f"[calibration] 项目 L2 阈值 {result['base_threshold']:.2f} → "
        f"{result['l2_threshold']:.2f}（显式 L2 评价样本 {result['sample_count']} 条；"
        f"本次新增/更新 {result['new_samples']} 条）"
    )
    print(f"[calibration] 已保存: {result['path']}")


def cmd_review(args) -> None:
    """会后评审 CLI mock：查看片段、写回 review、生成反馈摘要。"""
    from .pool.store import Pool

    pool = Pool(args.session)
    clips = pool.list_clips()
    if not clips:
        print(f"[review] 会话 {args.session} 没有可评审片段")
        return

    if args.summary:
        for i, clip in enumerate(clips, 1):
            _print_review_clip(clip, i)
        path = pool.write_feedback_summary()
        _review_summary(pool)
        print(f"[feedback] 摘要: {path}")
        _calibrate_project(pool, args.project)
        return

    if args.clip and args.status:
        updated = pool.update_review(args.clip, args.status, args.note)
        path = pool.write_feedback_summary()
        print(f"[review] 已更新 {updated['clip_id'][:8]} -> {updated['review']['status']}")
        if updated["review"]["note"]:
            print(f"[review] 备注: {updated['review']['note']}")
        _review_summary(pool)
        print(f"[feedback] 摘要: {path}")
        _calibrate_project(pool, args.project)
        return

    if args.clip and not args.status:
        matches = [c for c in clips if c["clip_id"] == args.clip or c["clip_id"].startswith(args.clip)]
        if len(matches) != 1:
            raise SystemExit(f"[review] --clip 必须匹配唯一片段: {args.clip}")
        _print_review_clip(matches[0])
        raise SystemExit("[review] 还需同时提供 --status useful|useless 和可选 --note")

    print(f"[review] 会后片段评审（{args.session}）；输入编号，q 退出")
    for i, clip in enumerate(clips, 1):
        _print_review_clip(clip, i)
    while True:
        choice = input("\n评审片段编号（q 退出）> ").strip()
        if choice.lower() == "q":
            break
        if not choice.isdigit() or not 1 <= int(choice) <= len(clips):
            print("请输入列表中的编号")
            continue
        clip = clips[int(choice) - 1]
        status = input("评价 [u=有用 / n=没用 / s=跳过] > ").strip().lower()
        if status == "s":
            continue
        status = {"u": "useful", "n": "useless"}.get(status)
        if not status:
            print("请输入 u、n 或 s")
            continue
        note = input("备注（可空）> ")
        pool.update_review(clip["clip_id"], status, note)
        clips = pool.list_clips()
        print(f"[review] 已写回 {clip['clip_id'][:8]} -> {status}")
    path = pool.write_feedback_summary()
    _review_summary(pool)
    print(f"[feedback] 摘要: {path}")
    _calibrate_project(pool, args.project)


def cmd_devices(args) -> None:
    from .capture.audio import AudioCapture

    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as p:
        default = p.get_default_wasapi_loopback()["index"]
        for info in p.get_loopback_device_info_generator():
            marker = " [系统默认]" if info["index"] == default else ""
            print(f"  [{info['index']}] {info['name']} ({int(info['maxInputChannels'])}ch @ {int(info['defaultSampleRate'])}Hz){marker}")


def cmd_terms(args) -> None:
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
    rc.add_argument("--audio-device", type=int, help="devices 列出的环回端点编号；默认跟随系统输出")
    rc.add_argument("--hd", action="store_true", help="命中后高清补录 ≤90s（帧缩至 1280 宽；帧率按临时预算折算，实测约 13-20fps）")
    rc.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    rc.add_argument("--device", default="cuda", choices=["cpu", "cuda"])

    dg = sub.add_parser("digest", help="会后生成薄/厚纪要")
    dg.add_argument("--session", required=True)
    dg.add_argument("--llm", action="store_true", help="调用 LLM 生成叙述版（需环境变量）")

    ar = sub.add_parser("archive", help="把已生成的纪要复制或更新到项目目录")
    ar.add_argument("--session", required=True)
    ar.add_argument("--project", required=True)

    rv = sub.add_parser("review", help="会后评审片段并写回评价反馈")
    rv.add_argument("--session", required=True)
    rv.add_argument("--clip", default=None, help="片段完整 id 或唯一前缀")
    rv.add_argument("--status", choices=["useful", "useless"], default=None)
    rv.add_argument("--note", default=None, help="评价备注")
    rv.add_argument("--summary", action="store_true", help="只查看并生成反馈摘要")
    rv.add_argument("--project", default=None, help="项目文件夹；把本会话 L2 评价纳入项目阈值校准")

    sub.add_parser("devices", help="列出环回设备")

    tm = sub.add_parser("terms", help="F1：从项目记忆自动抽取监听词表（术语+人名）")
    tm.add_argument("--memory", default=None, help="项目记忆文件夹（md/txt），文档层输入")
    tm.add_argument("--out", default=str(config.TERMS_FILE))
    tm.add_argument("--extra", default=None, help="手动热词文件（用户主动输入，永远保留）")
    tm.add_argument("--llm", action="store_true", help="文档语义层：本地小模型抽人名/角色/焦点（需 MEETINGVAR_LLM_*）")
    tm.add_argument("--repo", default=None, help="代码仓库路径（L0 工具层：符号抽取，零 LLM）")
    tm.add_argument("--govern", action="store_true", help="治理：监听表/纠错表分离（词形+常用词守卫），另写 <out>.correction.txt 与 <out>.govern.json")
    tm.add_argument("--api", action="store_true", help="仓库语义层：API 大模型只读关键文件候选摘要（需 MEETINGVAR_LLM_* 指向 API）")

    rs = sub.add_parser("rescan", help="把项目内全部归档纪要+文档重扫一遍，更新该项目热词库")
    rs.add_argument("project", help="项目根目录")
    rs.add_argument("--extra", default=None, help="手动词文件（#!keep 永久保留）")
    rs.add_argument("--llm", action="store_true", help="叠加本地/云端小模型语义抽取")

    register_go(sub)

    args = ap.parse_args()
    {"replay": cmd_replay, "transcribe": cmd_transcribe, "record": cmd_record, "digest": cmd_digest,
     "review": cmd_review, "archive": cmd_archive, "devices": cmd_devices, "terms": cmd_terms,
     "go": cmd_go, "rescan": cmd_rescan}[args.cmd](args)


def cmd_rescan(args) -> None:
    from .memory.project_store import rescan_terms

    extra = load_terms(Path(args.extra)) if args.extra else []
    words, n_minutes = rescan_terms(Path(args.project), extra_terms=extra, use_llm=args.llm)
    print(f"[rescan] 扫描归档纪要 {n_minutes} 份 + 项目文档 -> 热词库 {len(words)} 词")
    print(f"[rescan] 位置: {Path(args.project) / 'meetingvar' / 'terms.txt'}")


if __name__ == "__main__":
    main()
