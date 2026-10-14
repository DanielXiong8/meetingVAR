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

    from .live import record_session
    from .monitor.hotwords import InterestScorer
    from .memory.calibration import load_threshold

    project = Path(args.project)
    memory_dir = project if project.is_dir() else project.parent
    repo_dir = project if (project / ".git").exists() or any(project.glob("*.py")) else None
    session_id = f"go_{time.time_ns()}"

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
    print(f"[calibration] 项目 L2 阈值: {l2_threshold:.2f}")

    # 后台增强只在采集资源成功初始化后启动。
    def on_session(session):
        if not args.llm:
            return

        def slow_extract():
            from .memory.extract import llm_extract
            result = llm_extract(memory_dir)
            if not result:
                print("[go] 语义层不可用，本次以快词表监听")
                return
            llm_terms, persons = result
            new_words = [word for word in llm_terms if word not in terms]
            for word in new_words:
                terms[word] = llm_terms[word]
            # 纠错词表和监听词表同步更新；不重复添加已有监听词。
            session.terms.extend(new_words)
            session.monitor.terms.extend(word for word in llm_terms if word not in session.monitor.terms)
            if focus:
                session.scorer = InterestScorer(focus + list(llm_terms), threshold=l2_threshold)
            for person in persons:
                print(f"  [go+] 人物: {person.get('name')} — {person.get('role', '?')}")
            print(f"[go+] 语义层热更新完成: +{len(new_words)} 词，本场即刻生效")

        threading.Thread(target=slow_extract, daemon=True).start()

    # 声纹名册：按项目存放（<项目>/meetingvar/speakers.json）——同一个项目里的常用人
    # 第一次点过名，下次开会自动识别。
    from .memory.project_store import project_dir as _project_dir
    from .memory.speaker_book import SpeakerBook

    book = SpeakerBook.load(_project_dir(memory_dir) / "speakers.json")
    if book.entries:
        print(f"[spk] 声纹名册：{len(book.entries)} 人（{'、'.join(e['name'] for e in book.entries)}），新说话人将先比对名册")

    summary = record_session(args, session_id, wordlist, focus, l2_threshold, on_session,
                             speaker_book=book)
    print(f"[done] session={session_id} / {summary['lines']} 句 / {summary['clips']} 片段 / 纠错 {len(summary['corrections'])} 处")

    # 项目归档：纪要自动进 <项目>/meetingvar/minutes/，供跨会话热词库演化
    # 零命中会话（零落盘）没有可归档内容——直接告知，不制造空纪要。
    if summary.get("transcript_json"):
        try:
            from .memory.project_store import archive_report, project_dir

            digest_(session_id, use_llm=False)  # 结构化版先落盘（LLM 版用户手动 digest --llm）
            archive_report(project, session_id)
            print(f"[go] 纪要已归档至 {project_dir(project) / 'minutes' / session_id}")
        except Exception as e:  # noqa: BLE001 — 归档失败不阻塞主流程
            print(f"[go] 归档跳过：{e}")
        print(f"[next] meetingvar digest --session {session_id} --llm")
        print(f'[next] meetingvar archive --session {session_id} --project "{project}"  （更新项目纪要副本）')
    else:
        print("[go] 本场零命中：按零落盘策略，全程内容已随会话结束丢弃（磁盘上没有留下任何会议记录）")


def register(sub) -> None:
    g = sub.add_parser("go", help="一条命令开会：拖入项目路径，秒级起监听，语义层后台增强")
    g.add_argument("project", help="项目文件夹（代码+文档混放也行）")
    g.add_argument("--no-repo", action="store_true", help="跳过代码符号抽取")
    g.add_argument("--no-screen", action="store_true")
    g.add_argument("--audio-device", type=int, help="devices 列出的环回端点编号；默认跟随系统输出")
    g.add_argument("--hd", action="store_true", help="命中后高清补录 ≤90s（帧缩至 1280 宽；帧率按临时预算折算）")
    g.add_argument("--llm", action="store_true", help="后台叠加本地/云端小模型语义抽取并热更新")
    g.add_argument("--no-ask-speakers", action="store_true", help="新说话人出现时不询问名字（默认询问）")
    g.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    g.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
