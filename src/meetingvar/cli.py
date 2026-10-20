"""meetingvar 命令行入口。

    meetingvar replay  --wav <file> [--terms data/terms.txt] [--memory <folder>] [--device cuda]
    meetingvar record  [--terms data/terms.txt] [--memory <folder>] [--no-screen] [--duration 600]
    meetingvar digest  --session <id> [--llm]
    meetingvar review  --session <id> [--clip <id>] [--status useful|useless]
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
    from .memory.files import SUPPORTED_TEXT_SUFFIXES, project_files, read_text_safely

    # 健壮读取（坑：严格 utf-8 曾被伪装成 .txt 的 zip 文件崩掉整个 go，界面只见"会议已结束"）
    docs = []
    for f in project_files(folder, SUPPORTED_TEXT_SUFFIXES):
        text = read_text_safely(f)
        if text:
            docs.append(text)
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
    from .memory.speaker_book import SpeakerBook
    from .memory.interest_context import project_context
    from .go import load_confirm_terms

    project = Path(args.memory) if args.memory else None
    terms = load_terms(Path(args.terms)) if args.terms else (load_confirm_terms(project) if project else [])
    session_id = f"live_{time.time_ns()}"
    context = project_context(project, terms) if project else "未指定项目；仅按用户关注事项和通用重要事件判断"
    book = SpeakerBook.load(config.DATA_DIR / "speakers.json")
    if book.entries:
        print(f"[spk] 声纹名册：{len(book.entries)} 人")
    summary = record_session(args, session_id, [], None, None, speaker_book=book,
                             correction_terms=terms, interest_context=context)
    print(f"[done] session={session_id} / {summary['lines']} 句 / {summary['clips']} 片段 / 纠错 {len(summary['corrections'])} 处")
    if summary.get("transcript_json"):
        print(f"[next] meetingvar digest --session {session_id}")
    else:
        print("[rec] 本场零命中：草稿转写已丢弃，未保存会议记录")


def cmd_agent_actions(args) -> None:
    """会后行动项 Agent：只读核对原句 → 审批 → 写入项目事实记忆。"""
    import json as _json

    from .agent.postmeeting import PostMeetingAgent, load_transcript

    transcript_path = config.POOL_DIR / args.session / "transcript.json"
    if not transcript_path.exists():
        raise SystemExit(f"[agent] 没有找到会话转写: {transcript_path}")
    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[agent] 项目文件夹无效: {project}")
    transcript = load_transcript(transcript_path)
    if args.json and not (args.yes or args.dry_run):
        raise SystemExit("[agent] --json 需要配合 --yes 或 --dry-run，避免在无人值守时等待审批")

    def approve(action, evidence):
        print("\n[候选行动项]")
        print(f"  任务: {action['title']}")
        print(f"  负责人: {action['owner'] or '未指定'} · 截止: {action['due'] or '未指定'}")
        for turn in evidence:
            print(f"  证据 [{turn['id']}] {turn['speaker']}: {turn['text'][:160]}")
        try:
            return input("保存到项目记忆？[y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    agent = PostMeetingAgent(
        session_id=args.session, transcript=transcript, project_root=project,
        approval=(lambda action, evidence: True) if (args.yes or args.dry_run) else approve,
        dry_run=args.dry_run, max_chars=args.max_chars,
    )
    report = agent.run()
    if args.json:
        print(_json.dumps(report, ensure_ascii=False), flush=True)
    else:
        print(f"[agent] ok={report['ok']} 候选={report['candidates']} 核对={report['verified']} "
              f"批准={report['approved']} 写入={report['written']} 跳过={report['skipped']} "
              f"分块={report['chunks']} 任务={report['task_id']}")
        for error in report["errors"]:
            print(f"  [error] {error}")
        print(f"[agent] 轨迹: {agent.trace.path}")
    if not report["ok"]:
        raise SystemExit(1)


def cmd_agent_ask(args) -> None:
    """只读项目记忆问答：本地检索事实，模型只依据事实回答并给引用。"""
    import json as _json

    from .agent.recall import MemoryQAAgent

    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[agent] 项目文件夹无效: {project}")
    agent = MemoryQAAgent(project_root=project, question=args.question, limit=args.limit)
    report = agent.run()
    if args.json:
        print(_json.dumps(report, ensure_ascii=False), flush=True)
    else:
        print(report["answer"])
        if report["sources"]:
            print("\n[引用]")
            for source in report["sources"]:
                print(f"  [{source['name']}] ({source['type']}, score={source['score']})")
        for error in report.get("errors", []):
            print(f"  [warning] {error}")
    if not report["ok"]:
        raise SystemExit(1)


def cmd_agent_focus(args) -> None:
    """会前关注清单：读项目材料，提出可判断的事件式关注点，审批后保存。"""
    import json as _json

    from .agent.focus import FocusAgent

    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[agent] 项目文件夹无效: {project}")
    if args.json and not (args.yes or args.dry_run):
        raise SystemExit("[agent] --json 需要配合 --yes 或 --dry-run")

    def approve(items):
        print("\n[建议关注清单]")
        for index, item in enumerate(items, 1):
            print(f"  {index}. {item['title']} — {item['why']}")
        try:
            return input("保存为会前关注清单？[y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    agent = FocusAgent(project_root=project, dry_run=args.dry_run,
                       approval=(lambda items: True) if (args.yes or args.dry_run) else approve,
                       max_chars=args.max_chars)
    report = agent.run()
    if args.json:
        print(_json.dumps(report, ensure_ascii=False), flush=True)
    else:
        for item in report["items"]:
            print(f"  - {item['title']}：{item['why']}")
        print(f"[agent] ok={report['ok']} saved={report['saved']} path={report['path']}")
        for error in report.get("errors", []):
            print(f"  [error] {error}")
    if not report["ok"]:
        raise SystemExit(1)


def cmd_agent_memory(args) -> None:
    """查看/检索项目 Agent 事实记忆。"""
    import json as _json

    from .agent.memory import FactStore

    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[agent] 项目文件夹无效: {project}")
    store = FactStore(project)
    facts = store.search(args.query, limit=args.limit) if args.query else store.list_facts(limit=args.limit)
    if args.json:
        payload = [{"name": f.get("name", ""), "description": f.get("description", ""),
                    "type": f.get("metadata", {}).get("type", ""),
                    "origin_meeting": f.get("metadata", {}).get("origin_meeting", ""),
                    "updated_at": f.get("metadata", {}).get("updated_at", "")} for f in facts]
        print(_json.dumps(payload, ensure_ascii=False), flush=True)
        return
    if not facts:
        print("[agent] 暂无匹配的项目事实")
        return
    for fact in facts:
        print(f"- [{fact.get('metadata', {}).get('type', '?')}] {fact.get('name', '')} — "
              f"{fact.get('description', '')}")


def cmd_agent_run(args) -> None:
    """Bounded ReAct research run with tool calls and verified citations."""
    import json as _json

    from .agent.loop import AgentLoop

    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[agent-run] 项目文件夹无效: {project}")
    loop = AgentLoop(project_root=project, question=args.question,
                     data_dir=config.DATA_DIR, mode=args.mode,
                     max_steps=args.max_steps, max_tool_calls=args.max_tools,
                     max_seconds=args.max_seconds)
    report = loop.run(resume_task_id=args.resume)
    if args.json:
        print(_json.dumps(report, ensure_ascii=False), flush=True)
    else:
        print(report.get("answer") or "（没有形成答案）")
        if report.get("citations"):
            print("\n[引用]")
            for citation in report["citations"]:
                if citation.get("kind") == "turn":
                    print(f"  turn {citation.get('session_id')} {citation.get('turn_ids')}"
                          + (f"：{citation['quote']}" if citation.get("quote") else ""))
                else:
                    print(f"  fact {citation.get('name')}")
        print(f"\n[agent] status={report.get('status')} stop={report.get('stop_reason')} "
              f"steps={report.get('steps')} tools={report.get('tool_calls')} "
              f"grounded={report.get('grounded')} mode={report.get('mode')}")
        usage = report.get("usage") or {}
        print(f"[agent] tokens prompt={usage.get('prompt_tokens', 0)} "
              f"completion={usage.get('completion_tokens', 0)} trace={report.get('trace')}")
        for error in report.get("errors", []):
            print(f"  [warning] {error}")
    if not report.get("ok"):
        raise SystemExit(1)


def cmd_agent_eval(args) -> None:
    """Run a golden question set through the same AgentLoop used by the product."""
    import json as _json

    from .agent.eval import evaluate, load_cases
    from .agent.loop import AgentLoop

    project = Path(args.project)
    cases_path = Path(args.cases)
    if not project.is_dir():
        raise SystemExit(f"[agent-eval] 项目文件夹无效: {project}")
    if not cases_path.is_file():
        raise SystemExit(f"[agent-eval] 问题集不存在: {cases_path}")
    cases = load_cases(cases_path)

    def runner(case):
        loop = AgentLoop(project_root=project, question=case.question,
                         data_dir=config.DATA_DIR, mode=args.mode,
                         max_steps=min(args.max_steps, case.max_steps))
        return loop.run()

    result = evaluate(cases, runner)
    if args.json:
        print(_json.dumps(result, ensure_ascii=False), flush=True)
        return
    summary = result["summary"]
    print(f"[agent-eval] cases={summary['total']} accuracy={summary['accuracy']} "
          f"grounded={summary['grounded_rate']} citation_p={summary['citation_precision']} "
          f"citation_r={summary['citation_recall']} refusal={summary['refusal_accuracy']}")
    for row in result["rows"]:
        print(f"  {row['case_id']}: ok={row['ok']} grounded={row['grounded']} "
              f"steps={row['steps']} tools={row['tool_calls']} stop={row['stop_reason']}")


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
        print("环回端点（--audio-device）：")
        for info in p.get_loopback_device_info_generator():
            marker = " [系统默认]" if info["index"] == default else ""
            print(f"  [{info['index']}] {info['name']} ({int(info['maxInputChannels'])}ch @ {int(info['defaultSampleRate'])}Hz){marker}")
        print("麦克风输入（--mic-device，缺省跟随系统默认麦克风）：")
        try:
            dmic = p.get_default_input_device_info()
            dmic_idx = dmic["index"] if not dmic.get("isLoopbackDevice") else None
        except Exception:  # noqa: BLE001
            dmic_idx = None
        for info in p.get_device_info_generator():
            if not info.get("maxInputChannels") or info.get("isLoopbackDevice"):
                continue
            marker = " [系统默认]" if info["index"] == dmic_idx else ""
            print(f"  [{info['index']}] {info['name']} ({int(info['maxInputChannels'])}ch @ {int(info['defaultSampleRate'])}Hz){marker}")


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
    rp.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])

    tr = sub.add_parser("transcribe", help="转写模式：跑全管线并把全场转写显式存为 txt（--out 必填）")
    tr.add_argument("--wav", required=True)
    tr.add_argument("--out", required=True, help="全场转写输出 txt（[时:分:秒] 说话人: 文本 每行一句）")
    tr.add_argument("--terms", default=None, help="监听词表文件；缺省时从 --memory 自动抽取")
    tr.add_argument("--correction-terms", default=None, help="严进纠错词表（治理后子集）；缺省回落监听表")
    tr.add_argument("--memory", default=None, help="项目记忆文件夹（L2 兴趣文档）")
    tr.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])

    rc = sub.add_parser("record", help="实时采集（系统声音）")
    rc.add_argument("--terms", default=None, help="词表文件；缺省时从 --memory 自动抽取")
    rc.add_argument("--memory", default=None)
    rc.add_argument("--no-screen", action="store_true")
    rc.add_argument("--audio-device", type=int, help="devices 列出的环回端点编号；默认跟随系统输出")
    rc.add_argument("--source", choices=["both", "loopback", "mic"], default="both",
                    help="采集源：both=系统声音+麦克风（默认，两头都录）/ loopback=仅系统声音 / mic=仅麦克风")
    rc.add_argument("--mic-device", type=int, help="麦克风设备编号；缺省跟随系统默认麦克风")
    rc.add_argument("--hd", action="store_true", help="命中后高清补录 ≤90s（帧缩至 1280 宽；帧率按临时预算折算，实测约 13-20fps）")
    rc.add_argument("--no-ask-speakers", action="store_true", help="新说话人出现时不询问名字（默认询问）")
    rc.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    rc.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    rc.add_argument("--asr", choices=["local", "xfyun", "xfyun_rtasr"], default="local",
                    help="local=本地分块；xfyun=IAT分块；xfyun_rtasr=讯飞RTASR连续转写")
    rc.add_argument("--xfyun-cred", default=None, help="对应讯飞服务凭证文件或目录")
    rc.add_argument("--interest", choices=["api", "manual"], default="manual",
                    help="API兴趣事件判断或仅手动保存；不以专业词或相似度直接保存")

    ag = sub.add_parser("agent-actions", help="会后行动项 Agent：核对原句、审批后写入项目记忆")
    ag.add_argument("--session", required=True, help="已保存的会话 ID")
    ag.add_argument("--project", required=True, help="把行动项写入该项目的 agent_memory")
    ag.add_argument("--dry-run", action="store_true", help="只核对和展示，不写项目记忆")
    ag.add_argument("--yes", action="store_true", help="自动批准所有候选（适合脚本；人工使用请省略）")
    ag.add_argument("--json", action="store_true", help="输出单行 JSON 报告")
    ag.add_argument("--max-chars", type=int, default=12000, help="每次模型调用的转写字符上限")

    aq = sub.add_parser("agent-ask", help="只读项目记忆问答（带事实引用）")
    aq.add_argument("--project", required=True)
    aq.add_argument("--question", required=True)
    aq.add_argument("--limit", type=int, default=8)
    aq.add_argument("--json", action="store_true")

    af = sub.add_parser("agent-focus", help="会前关注清单：读项目材料、审批后保存")
    af.add_argument("--project", required=True)
    af.add_argument("--dry-run", action="store_true")
    af.add_argument("--yes", action="store_true")
    af.add_argument("--json", action="store_true")
    af.add_argument("--max-chars", type=int, default=12000)

    am = sub.add_parser("agent-memory", help="查看/检索项目 Agent 事实记忆")
    am.add_argument("--project", required=True)
    am.add_argument("--query", default=None)
    am.add_argument("--limit", type=int, default=20)
    am.add_argument("--json", action="store_true")

    arun = sub.add_parser("agent-run", help="有界 ReAct Agent：模型自主选工具后回答并校验引用")
    arun.add_argument("--project", required=True)
    arun.add_argument("--question", required=True)
    arun.add_argument("--mode", choices=["auto", "native", "json"], default="auto")
    arun.add_argument("--max-steps", type=int, default=8)
    arun.add_argument("--max-tools", type=int, default=12)
    arun.add_argument("--max-seconds", type=float, default=180.0)
    arun.add_argument("--resume", default=None, help="从持久化的 task id 继续")
    arun.add_argument("--json", action="store_true")

    aeval = sub.add_parser("agent-eval", help="跑 Agent 黄金问题集并输出指标")
    aeval.add_argument("--project", required=True)
    aeval.add_argument("--cases", required=True)
    aeval.add_argument("--mode", choices=["auto", "native", "json"], default="auto")
    aeval.add_argument("--max-steps", type=int, default=8)
    aeval.add_argument("--json", action="store_true")

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
    tm.add_argument("--curate", action="store_true", help="选词人模式（推荐）：LLM 直接产出 10~20 个核心关键词（带理由），L0/L1 仅作参考资料")
    tm.add_argument("--api", action="store_true", help="仓库语义层：API 大模型只读关键文件候选摘要（需 MEETINGVAR_LLM_* 指向 API）")

    rs = sub.add_parser("rescan", help="把项目内全部归档纪要+文档重扫一遍，更新该项目热词库")
    rs.add_argument("project", help="项目根目录")
    rs.add_argument("--extra", default=None, help="手动词文件（#!keep 永久保留）")
    rs.add_argument("--llm", action="store_true", help="叠加本地/云端小模型语义抽取")

    un = sub.add_parser("understand", help="全库理解：分层读完整个文件夹，产出候选词/人物（不自动写词表）")
    un.add_argument("folder", help="项目文件夹")
    un.add_argument("--max", type=int, default=0, help="只读前 N 块（0=全部；小样试跑用）")
    un.add_argument("--write", action="store_true", help="候选词写入 <folder>/meetingvar/terms.txt（#!keep，下次开会自动采用）")
    un.add_argument("--json", action="store_true", help="结果以单行 JSON 输出（桥接/UI 调用）")

    bn = sub.add_parser("bench", help="离线事件评测：B0 热词基线 / B3 关注点+滑窗裁判（不碰真实保存链路）")
    bn.add_argument("--transcript", required=True, help="转写 txt（[HH:MM:SS] 说话人: 文本；须与标注同一份）")
    bn.add_argument("--labels", default=None, help="标注 JSON（benchmark_v0 的 events 或 [start,end] 列表）")
    bn.add_argument("--variant", choices=["b0", "b3"], default="b0")
    bn.add_argument("--terms", default=None, help="b0 用词表文件")
    bn.add_argument("--focus", default=None, help="b3 用关注清单（focus_list_v0.json）")
    bn.add_argument("--window", type=float, default=120.0)
    bn.add_argument("--stride", type=float, default=30.0)
    bn.add_argument("--chunk", type=float, default=8.0, help="b0 判定块长（秒）")
    bn.add_argument("--merge-gap", type=float, default=30.0, help="事件级合并间隔（秒）")
    bn.add_argument("--tol", type=float, default=45.0, help="匹配容差（秒）")
    bn.add_argument("--hours", type=float, default=0.0, help="会议时长；0=从转写推断")
    bn.add_argument("--out", default=None, help="结果 JSON 输出路径")

    register_go(sub)

    args = ap.parse_args()
    {"replay": cmd_replay, "transcribe": cmd_transcribe, "record": cmd_record, "digest": cmd_digest,
     "review": cmd_review, "archive": cmd_archive, "devices": cmd_devices, "terms": cmd_terms,
     "go": cmd_go, "rescan": cmd_rescan, "understand": cmd_understand, "bench": cmd_bench,
     "agent-actions": cmd_agent_actions, "agent-ask": cmd_agent_ask,
     "agent-focus": cmd_agent_focus, "agent-memory": cmd_agent_memory,
     "agent-run": cmd_agent_run, "agent-eval": cmd_agent_eval}[args.cmd](args)


def cmd_understand(args) -> None:
    """全库分层阅读：读完整个文件夹（分块+逐块校验+确定性合并），产出候选词/人物。

    用哪个厂商由 MEETINGVAR_LLM_* 决定（本地模型=零出网；API=块文本出网）——
    产品里这个选择交给用户在设置页做，本命令与厂商无关。
    """
    import json as _json

    from .memory.probe import plan, understand, write_terms

    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"[understand] 不是文件夹: {folder}")
    p = plan(folder)
    if not args.json:
        print(f"[understand] 全库 {p['chars']} 字 / {len(p['chunks'])} 块"
              f"（本地 4B 预估 {p['est_local_minutes']:.0f} 分钟；云端模型快得多，费用按所选厂商）")
        for n in p["notes"]:
            print(f"  [note] {n}")
    prog = (lambda m: print(f"[progress] {m}", flush=True)) if args.json \
        else (lambda m: print(f"[understand] {m}", flush=True))
    r = understand(folder, max_chunks=args.max or None, progress=prog)
    if args.json:
        print(_json.dumps(r, ensure_ascii=False), flush=True)
        return
    print(f"[understand] ok={r['ok']} · 覆盖 {r['covered_chars']}/{r['unique_chars']} 字"
          f"（{r['coverage_pct']}%）· 缓存命中 {r['cache_hits']} 块")
    if r.get("error"):
        print(f"  [error] {r['error']}")
    if r.get("overview"):
        print(f"  一句话理解：{r['overview']}")
    for t in r["terms"][:30]:
        print(f"  {t['term']}  x{t['count']}（{t['chunks']} 块）")
    for p_ in r["persons"][:15]:
        print(f"  人物 {p_['name']}  x{p_['count']}")
    if args.write and r["terms"]:
        out = write_terms(folder, [t["term"] for t in r["terms"][:30]])
        print(f"[understand] 已写入 {min(30, len(r['terms']))} 词（#!keep）→ {out}")


def cmd_bench(args) -> None:
    """离线事件级评测：B0=现役热词基线 / B3=关注点+滑窗 LLM 裁判（不碰真实保存链路）。

    转写格式：[HH:MM:SS] 说话人: 文本（同一份转写必须同时用于标注与评测，否则 turn_id 对不上）。
    指标口径见 eval/matching.py：P/R/F1/**F2**（主看召回）/每小时误触发；标注支持
    benchmark_v0 的 {"events":[{start_time,end_time,...}]} 或 [{"start","end"}]。
    """
    import json as _json

    from .eval.judge import judge_transcript, load_focus, load_labels, merge_hits, parse_transcript
    from .eval.matching import evaluate_preds, merge_predictions

    turns = parse_transcript(Path(args.transcript))
    if not turns:
        raise SystemExit("[bench] 转写为空或格式不匹配（需要 [HH:MM:SS] 说话人: 文本）")
    duration = turns[-1]["t1"]
    hours = args.hours or duration / 3600.0
    detail: dict = {}
    if args.variant == "b0":
        from .monitor.hotwords import HotwordMonitor

        terms = load_terms(Path(args.terms)) if args.terms else []
        if not terms:
            raise SystemExit("[bench] b0 需要 --terms 词表文件")
        mon = HotwordMonitor(terms)
        chunk = max(2.0, float(args.chunk))
        preds: list[tuple[float, float]] = []
        t = 0.0
        while t < duration:
            sel = [x for x in turns if t <= x["t0"] < t + chunk]
            text = " ".join(x["text"] for x in sel)
            if text.strip() and mon.check(text, now=t + chunk):
                preds.append((t, t + chunk))
            t += chunk
        detail = {"variant": "b0", "terms": len(terms), "raw_hits": len(preds)}
    else:
        focus = load_focus(Path(args.focus)) if args.focus else []
        hits = judge_transcript(turns, window=float(args.window), stride=float(args.stride),
                                focus=focus, progress=lambda m: print(f"[progress] {m}", flush=True))
        merged_hits = merge_hits(hits, gap=float(args.merge_gap))
        preds = [(h["start"], h["end"]) for h in merged_hits]
        detail = {
            "variant": "b3", "windows": sum(1 for _ in hits),
            "hit_windows": sum(1 for h in hits if h["hit"]),
            "merged_events": len(merged_hits),
            "events": [{k: h.get(k) for k in ("start", "end", "event_type", "event_status",
                                              "focus_id", "evidence_turn_ids", "evidence_quote",
                                              "reason", "confidence", "owner", "deadline")}
                       for h in merged_hits],
        }
    shown = merge_predictions(preds, float(args.merge_gap)) if args.variant == "b0" else preds
    result = {"transcript": str(args.transcript), "turns": len(turns),
              "duration_s": round(duration, 1), "hours": round(hours, 3), **detail,
              "predictions": [[round(a, 1), round(b, 1)] for a, b in shown]}
    if args.labels:
        gold = load_labels(Path(args.labels))
        result["metrics"] = evaluate_preds([(g["start"], g["end"]) for g in gold], preds,
                                           meeting_hours=hours, merge_gap=float(args.merge_gap),
                                           tol=float(args.tol))
        result["gold_events"] = len(gold)
    out = _json.dumps(result, ensure_ascii=False, indent=1)
    print(out, flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")


def cmd_rescan(args) -> None:
    from .memory.project_store import rescan_terms

    extra = load_terms(Path(args.extra)) if args.extra else []
    words, n_minutes = rescan_terms(Path(args.project), extra_terms=extra, use_llm=args.llm)
    print(f"[rescan] 扫描归档纪要 {n_minutes} 份 + 项目文档 -> 热词库 {len(words)} 词")
    print(f"[rescan] 位置: {Path(args.project) / 'meetingvar' / 'terms.txt'}")


if __name__ == "__main__":
    main()
