"""Open a project meeting: confirmed terminology correction and API event capture."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .cli import load_terms


def load_confirm_terms(project: Path) -> list[str]:
    """项目确认词（<项目>/meetingvar/terms.txt 里的 #!keep 词；全库理解/人工确认写入）。"""
    f = Path(project) / "meetingvar" / "terms.txt"
    return load_terms(f) if f.exists() else []


def cmd_go(args) -> None:
    from .report.generate import digest as digest_

    from .live import record_session
    from .memory.interest_context import project_context

    project = Path(args.project)
    memory_dir = project if project.is_dir() else project.parent
    session_id = f"go_{time.time_ns()}"
    kept = load_confirm_terms(memory_dir)
    print(f"[go] 专业词纠错: {len(kept)} 个确认词；不执行结巴/相似度自动保存")
    if getattr(args, "llm", False):
        args.interest = "api"
    if not hasattr(args, "interest"):
        args.interest = "manual"
    print(f"[go] 兴趣判断: {'API事件判断' if args.interest == 'api' else '仅手动保存'}")
    context = project_context(memory_dir, kept)

    # 声纹名册：按项目存放（<项目>/meetingvar/speakers.json）——同一个项目里的常用人
    # 第一次点过名，下次开会自动识别。
    from .memory.project_store import project_dir as _project_dir
    from .memory.speaker_book import SpeakerBook

    book = SpeakerBook.load(_project_dir(memory_dir) / "speakers.json")
    if book.entries:
        print(f"[spk] 声纹名册：{len(book.entries)} 人（{'、'.join(e['name'] for e in book.entries)}），新说话人将先比对名册")

    summary = record_session(args, session_id, [], None, None, speaker_book=book,
                             correction_terms=kept, interest_context=context)
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
    g = sub.add_parser("go", help="项目会议：专业词纠错、API兴趣判断或手动取证")
    g.add_argument("project", help="项目文件夹（代码+文档混放也行）")
    g.add_argument("--no-repo", action="store_true", help="跳过代码符号抽取")
    g.add_argument("--no-screen", action="store_true")
    g.add_argument("--audio-device", type=int, help="devices 列出的环回端点编号；默认跟随系统输出")
    g.add_argument("--source", choices=["both", "loopback", "mic"], default="both",
                   help="采集源：both=系统声音+麦克风（默认）/ loopback=仅系统声音 / mic=仅麦克风")
    g.add_argument("--mic-device", type=int, help="麦克风设备编号；缺省跟随系统默认麦克风")
    g.add_argument("--hd", action="store_true", help="命中后高清补录 ≤90s（帧缩至 1280 宽；帧率按临时预算折算）")
    g.add_argument("--llm", action="store_true", help="兼容选项：启用API兴趣判断，不再扩展自动触发词表")
    g.add_argument("--interest", choices=["api", "manual"], default="manual",
                   help="api=大模型判断兴趣事件；manual=仅手动保存；专业词始终只用于纠错")
    g.add_argument("--no-ask-speakers", action="store_true", help="新说话人出现时不询问名字（默认询问）")
    g.add_argument("--duration", type=int, default=0, help="秒，0=直到 Ctrl+C")
    g.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
    g.add_argument("--asr", choices=["local", "xfyun", "xfyun_rtasr"], default="local",
                   help="local=本地分块；xfyun=IAT分块；xfyun_rtasr=讯飞RTASR连续转写")
    g.add_argument("--xfyun-cred", default=None, help="对应讯飞服务凭证文件或目录")
