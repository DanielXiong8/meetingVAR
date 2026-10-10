"""meetingvar go <项目路径>：一条命令进入"马上开会"模式。

时序设计（针对"5 分钟后开会"场景）：
  t+0s    L0 工具层 + L1 jieba 抽取（秒级）→ 立即出可用词表
  t+5s    录音开始（不等任何模型）——开会不等人
  t+~3min 本地小模型后台抽完 → 热更新词表与兴趣画像（正在进行的会议即刻生效）
用户全程只敲一条命令。
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from . import config
from .cli import load_memory_docs, load_terms


def cmd_go(args) -> None:
    from .report.generate import digest as digest_

    from .capture.audio import AudioCapture
    from .capture.screen import ScreenCapture
    from .monitor.hotwords import InterestScorer
    from .pipeline import Session
    from .memory.calibration import load_threshold

    project = Path(args.project)
    memory_dir = project if project.is_dir() else project.parent
    repo_dir = project if (project / ".git").exists() or any(project.glob("*.py")) else None
    session_id = f"go_{int(time.time())}"

    # ---- t+0：快抽取（工具层 + jieba，秒级） ----
    terms: dict[str, list[str]] = {}
    if args.no_repo is False and repo_dir:
        from .memory.repo_extract import repo_terms

        r, _cand = repo_terms(repo_dir)
        for w, s in r.items():
            terms.setdefault(w, []).extend(s)
        print(f"[go] L0 工具层: {len(r)} 词")
    from .memory.extract import extract_terms

    dterms = extract_terms(memory_dir)
    for w, s in dterms.items():
        terms.setdefault(w, []).extend(s)
    print(f"[go] L1 jieba: 共 {len(terms)} 词 — 立即可用")
    wordlist = list(terms)
    if not wordlist:
        print("[go] 警告：项目里没抽到词，本次只有手动 VAR（回车）兜底")

    focus = load_memory_docs(memory_dir)
    l2_threshold = load_threshold(memory_dir)
    scorer_holder = {"scorer": InterestScorer(focus, threshold=l2_threshold) if focus else None}
    print(f"[calibration] 项目 L2 阈值: {l2_threshold:.2f}")

    # ---- t+5s：录音立即开始 ----
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
        session_id, terms=wordlist, focus_docs=focus,
        device=args.device, screen_capture=screen, echo=True, l2_threshold=l2_threshold,
    )
    session.hd = hd
    session.scorer = scorer_holder["scorer"]
    print(f"[rec] 会议监听开始。回车=手动 VAR，Ctrl+C 结束。语义层后台增强中…")

    # ---- 后台：慢抽取 → 热更新 ----
    if args.llm:
        def _slow_extract():
            from .memory.extract import llm_extract

            r = llm_extract(memory_dir)
            if not r:
                print("[go] 语义层不可用（本地模型未启动或未配置），本次以快词表监听")
                return
            llm_terms, persons = r
            new_words = []
            for w, srcs in llm_terms.items():
                if w not in terms:
                    terms[w] = srcs
                    new_words.append(w)
                session.monitor.terms.append(w)
            if scorer_holder["scorer"]:
                docs = focus + [w for w in llm_terms]
                scorer_holder["scorer"] = InterestScorer(docs, threshold=l2_threshold)
                session.scorer = scorer_holder["scorer"]
            for p in persons:
                print(f"  [go+] 人物: {p.get('name')} — {p.get('role', '?')}")
            print(f"[go+] 语义层热更新完成: +{len(new_words)} 词，本场即刻生效")

        threading.Thread(target=_slow_extract, daemon=True).start()

    # ---- 主循环：与 cmd_record 相同 ----
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
            if pcm:
                session.ingest_pcm(pcm, since)
                last_ts = since + len(pcm) / 2 / config.ASR_RATE
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            summary = session.finish()
        finally:
            try:
                audio.stop()
            finally:
                if screen:
                    screen.stop()
    print(f"[done] {summary['lines']} 句 / {summary['clips']} 片段 / 纠错 {len(summary['corrections'])} 处")

    # 项目归档：纪要自动进 <项目>/meetingvar/minutes/，供跨会话热词库演化
    try:
        from .memory.project_store import archive_report, project_dir

        digest_(session_id, use_llm=False)  # 结构化版先落盘（LLM 版用户手动 digest --llm）
        archive_report(project, session_id)
        print(f"[go] 纪要已归档至 {project_dir(project) / 'minutes' / session_id}")
    except Exception as e:  # noqa: BLE001 — 归档失败不阻塞主流程
        print(f"[go] 归档跳过：{e}")
    print(f"[next] meetingvar digest --session {session_id} --llm  （重出叙述版会覆盖归档）")


def register(sub) -> None:
    g = sub.add_parser("go", help="一条命令开会：拖入项目路径，秒级起监听，语义层后台增强")
    g.add_argument("project", help="项目文件夹（代码+文档混放也行）")
    g.add_argument("--no-repo", action="store_true", help="跳过代码符号抽取")
    g.add_argument("--no-screen", action="store_true")
    g.add_argument("--hd", action="store_true", help="命中后 30fps 高清补录 90s")
    g.add_argument("--llm", action="store_true", help="后台叠加本地/云端小模型语义抽取并热更新")
    g.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    g.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
