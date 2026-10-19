"""Engine subprocess bridge: progress lines followed by one final JSON object.

Preview/planning inspect a project, test_llm uses only a fixed synthetic prompt,
and full successful understanding atomically publishes its verified context.

Usage (the desktop backend supplies the effective MEETINGVAR_LLM_* environment):
    <engine_python> engine_bridge.py test_llm
    <engine_python> engine_bridge.py understand_plan <folder>
    <engine_python> engine_bridge.py understand <folder> [max_chunks]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_TREE_MAX_NODES = 800
_TREE_MAX_DEPTH = 6


def _build_tree(root: Path):
    """VSCode 风格文件树（含被跳过的非文本文件清单）。深度/节点数封顶。"""
    from meetingvar.memory.files import SKIP_DIRS, read_text_safely

    skipped: list[str] = []
    budget = [_TREE_MAX_NODES]

    def walk(d: Path, depth: int) -> list[dict]:
        if depth > _TREE_MAX_DEPTH or budget[0] <= 0:
            return []
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return []
        nodes = []
        for p in entries:
            if budget[0] <= 0:
                break
            if p.is_dir() and (p.name.lower() in SKIP_DIRS or p.is_symlink()):
                continue
            budget[0] -= 1
            try:
                if p.is_dir():
                    nodes.append({"name": p.name, "dir": True, "children": walk(p, depth + 1)})
                else:
                    node = {"name": p.name, "dir": False, "size": p.stat().st_size}
                    if p.suffix.lower() in {".txt", ".md"} and read_text_safely(p) is None:
                        skipped.append(p.name)
                    nodes.append(node)
            except OSError:
                continue
        return nodes

    return walk(root, 1), skipped


def cmd_preview(folder: Path) -> dict:
    from meetingvar.memory.extract import extract_terms
    from meetingvar.memory.files import project_files

    result: dict = {
        "folder": str(folder),
        "doc_count": 0,
        "terms": {},
        "term_count": 0,
        "persons": [],
        "repo": {"is_repo": False, "terms": 0, "top": {}},
        "tree": [],
        "skipped": [],
        "notes": [],
    }
    if not folder.is_dir():
        result["notes"].append("路径不是文件夹")
        return result

    try:
        result["doc_count"] = sum(
            1 for _ in project_files(folder, {".md", ".txt", ".pdf", ".pptx", ".docx"})
        )
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"文档扫描失败: {type(exc).__name__}")

    try:
        result["tree"], result["skipped"] = _build_tree(folder)
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"文件树构建失败: {type(exc).__name__}")

    try:
        terms = extract_terms(folder)
        result["term_count"] = len(terms)
        result["terms"] = {w: list(srcs)[:2] for w, srcs in list(terms.items())[:40]}
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"词表抽取失败: {type(exc).__name__}")

    try:
        is_repo = (folder / ".git").exists() or any(folder.glob("*.py"))
        if is_repo:
            from meetingvar.memory.repo_extract import repo_terms

            r, _cand = repo_terms(folder)
            result["repo"] = {
                "is_repo": True,
                "terms": len(r),
                "top": {w: list(srcs)[:2] for w, srcs in list(r.items())[:20]},
            }
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"仓库扫描失败: {type(exc).__name__}")

    persons_file = folder / "meetingvar" / "persons.json"
    if persons_file.exists():
        try:
            data = json.loads(persons_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                result["persons"] = [str(p.get("name", "")) for p in data if isinstance(p, dict) and p.get("name")]
        except Exception:  # noqa: BLE001
            pass
    return result


def cmd_check() -> dict:
    import platform

    import meetingvar

    return {
        "ok": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "meetingvar": getattr(meetingvar, "__version__", "dev"),
    }


def _find_cred_files(xfyun_dir: Path) -> dict:
    """在讯飞文件夹里认领两类 .env（分类逻辑见 meetingvar.transcribe.xfyun_cred；不打印密钥）。"""
    from meetingvar.transcribe.xfyun_cred import scan

    out: dict = {"iat": None, "lfasr": None, "files": []}
    for hit in scan(Path(xfyun_dir)):
        out["files"].append({"path": hit["path"], "kind": hit["kind"]})
        if out[hit["kind"]] is None:
            out[hit["kind"]] = hit["path"]
    return out


def _probe_iat(cred_path: Path) -> dict:
    """1s 静音走一次真实握手：验证鉴权与配额通路（静音无文本，只关心错误码）。"""
    import asyncio

    from meetingvar.transcribe import xfyun_iat as xi

    creds = xi.load_creds(Path(cred_path))
    errors: list[str] = []

    async def probe():
        async with xi._ws_connect(xi.ws_url(creds), max_size=2**20, open_timeout=10) as ws:
            async def recv():
                try:
                    async for m in ws:
                        try:
                            d = json.loads(m)
                        except ValueError:
                            continue
                        if d.get("code"):
                            errors.append(f"{d.get('code')}: {d.get('message', '')}")
                        if (d.get("data") or {}).get("status") == 2:
                            return
                except Exception:  # noqa: BLE001
                    pass

            rt = asyncio.create_task(recv())
            import base64

            silence = base64.b64encode(b"\x00" * 32000).decode()  # 1s 16k s16
            for st in (0, 2):
                await ws.send(json.dumps({
                    "common": {"app_id": creds["APPID"]},
                    "business": {"language": "zh_cn", "domain": "iat", "accent": "mandarin"},
                    "data": {"status": st, "format": "audio/L16;rate=16000",
                             "encoding": "raw", "audio": silence if st == 0 else ""},
                }))
            try:
                await asyncio.wait_for(rt, timeout=15)
            except asyncio.TimeoutError:
                pass

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(probe())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if errors:
        return {"ok": False, "error": errors[0]}
    return {"ok": True}


def cmd_check_xfyun(xfyun_dir: Path) -> dict:
    found = _find_cred_files(xfyun_dir)
    result: dict = {
        "ok": bool(found["iat"] or found["lfasr"]),
        "dir": str(xfyun_dir),
        "rtasr": None,
        "iat": None,
        "lfasr": None,
        "files": found["files"],
        "notes": ["仅检查配置格式；连接、配额和识别质量尚未验证"],
    }
    from meetingvar.transcribe.xfyun_rtasr import find_rtasr, load_creds as rt_load
    try:
        rt_path = find_rtasr(xfyun_dir)
        if rt_path is not None:
            rt_load(rt_path)
            result["rtasr"] = {"valid": True}
            result["ok"] = True
        else:
            result["notes"].append("未找到实时语音转写RTASR凭证")
    except (OSError, ValueError):
        result["rtasr"] = {"valid": False, "error": "RTASR凭证缺失、格式错误或有多个候选"}
    if found["iat"]:
        probe = {"valid": False}
        try:
            from meetingvar.transcribe.xfyun_iat import load_creds

            load_creds(Path(found["iat"]))
            probe = {"valid": True}
        except Exception as exc:  # noqa: BLE001
            probe = {"valid": False, "error": str(exc)[:120]}
        result["iat"] = {"path": found["iat"], **probe}
    else:
        result["notes"].append("未找到语音听写凭证（需含 APPID / APIKey / APISecret 的 .env）")
    if found["lfasr"]:
        try:
            from meetingvar.transcribe.xfyun_lfasr import load_creds as lf_load

            lf_load(Path(found["lfasr"]))
            result["lfasr"] = {"path": found["lfasr"], "valid": True}
        except Exception as exc:  # noqa: BLE001
            result["lfasr"] = {"path": found["lfasr"], "valid": False, "error": str(exc)[:120]}
    else:
        result["notes"].append("未找到录音文件转写凭证（需含 APPID / SecretKey 的 .env）")
    return result


def _to_wav16k(media: Path, tmp_dir: Path) -> Path:
    """任意媒体 → 16k 单声道 wav（ffmpeg）；已是 16k 单声道 wav 则原样返回。"""
    if media.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(media), "rb") as w:
                if w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2:
                    return media
        except Exception:  # noqa: BLE001
            pass
    from meetingvar.capture.hd import _ffmpeg

    ff = _ffmpeg()
    if not ff:
        raise RuntimeError("未找到 ffmpeg，无法从该格式抽取音频（wav 16k 单声道可直接用）")
    out = tmp_dir / (media.stem + ".16k.wav")
    import subprocess

    r = subprocess.run(
        [ff, "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(out)],
        capture_output=True, text=True, errors="replace", timeout=1800,
    )
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg 抽取失败: {(r.stderr or '')[-200:]}")
    return out


def cmd_transcribe_file(media: Path, xfyun_dir: Path, out_path: Path | None) -> dict:
    """媒体文件 → 讯飞录音文件转写 → 写 <stem>.transcript.txt。进度以 [progress] 行前置。"""
    import tempfile
    import wave

    from meetingvar.transcribe.xfyun_lfasr import fmt_lines, transcribe_file

    found = _find_cred_files(xfyun_dir)
    if not found["lfasr"]:
        return {"ok": False, "error": "未找到录音文件转写凭证（讯飞文件夹里需含 APPID/SecretKey 的 .env）"}
    if not media.exists():
        return {"ok": False, "error": f"文件不存在: {media}"}

    def progress(msg: str) -> None:
        print(f"[progress] {msg}", flush=True)

    with tempfile.TemporaryDirectory(prefix="mv_tf_") as td:
        progress("抽取音频…")
        wav = _to_wav16k(media, Path(td))
        with wave.open(str(wav), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        result = transcribe_file(wav, Path(found["lfasr"]), duration_s=dur, progress=progress)

    out = out_path or media.with_suffix("").with_name(media.stem + ".transcript.txt")
    header = [
        f"# 转写稿：{media.name}",
        f"# 时长: {dur/60:.1f} 分钟 | orderId: {result['order_id']} | 讯飞录音文件转写（含说话人分离）",
        "",
    ]
    out.write_text("\n".join(header) + fmt_lines(result["lines"]), encoding="utf-8")
    progress(f"完成：{out}")
    return {
        "ok": True,
        "out": str(out),
        "lines": len(result["lines"]),
        "order_id": result["order_id"],
        "duration_s": round(dur, 1),
    }


def cmd_test_llm() -> dict:
    """One synthetic generation with the same client and environment as reading.

    No project text is used. The subprocess receives the effective provider
    settings through MEETINGVAR_LLM_* environment variables.
    """
    from meetingvar import config
    from meetingvar.llm import chat_result, error_result, parse_json_object

    reply = chat_result('Return exactly this JSON object: {"ok":true}. '
                        'Do not include markdown or any other text.',
                        temperature=0.2, timeout=120)
    if reply["ok"]:
        data = parse_json_object(reply["content"])
        if data is None or set(data) != {"ok"} or data["ok"] is not True:
            reply = error_result("invalid_json", finish_reason=reply["finish_reason"], usage=reply["usage"])
    # Generated content is unnecessary for this test and must not leak through
    # the bridge protocol, even when a service returns an unexpected answer.
    return {**{k: v for k, v in reply.items() if k != "content"}, "model": config.LLM_MODEL}


def cmd_understand_plan(folder: Path) -> dict:
    """Offline budget plan; retain configuration failures returned by the planner."""
    from meetingvar.memory.probe import plan

    return plan(Path(folder))


def cmd_understand(folder: Path, max_chunks: int = 0) -> dict:
    """全库分层阅读（UI 后台任务）：进度以 [progress] 行前置，最后单行 JSON。

    厂商无关：MEETINGVAR_LLM_* 指向本地模型=零出网；指向自定义 API=块文本出网。
    """
    from meetingvar.memory.probe import atomic_write_json, understand

    def progress(msg: str) -> None:
        print(f"[progress] {msg}", flush=True)

    r = understand(Path(folder), max_chunks=max_chunks or None, progress=progress)
    # A failed run or an explicitly requested sample never publishes context,
    # even if that sample happens to cover every chunk of a small project.
    if r.get("ok") and r.get("complete") and max_chunks == 0:
        from meetingvar.memory.interest_context import context_from_result

        out = Path(folder) / "meetingvar" / "interest_context.json"
        try:
            atomic_write_json(out, context_from_result(r))
        except OSError:
            r.update(ok=False, error_code="context_write_failed",
                     error="理解结果未能写入，已有项目上下文已保留")
    return r


def cmd_write_terms(folder: Path, terms_file: Path) -> dict:
    """把确认词写入 <folder>/meetingvar/terms.txt（逐行 #!keep；go 下次开会自动并入）。"""
    import json as _json

    from meetingvar.memory.probe import write_terms

    try:
        terms = _json.loads(Path(terms_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"词表 JSON 读取失败: {exc}"}
    if not isinstance(terms, list) or not terms:
        return {"ok": False, "error": "词表为空"}
    out = write_terms(Path(folder), [str(t) for t in terms])
    return {"ok": True, "out": str(out), "count": len(terms)}


def cmd_agent_status(folder: Path) -> dict:
    import json as _json

    from meetingvar.agent.memory import FactStore

    store = FactStore(folder)
    facts = store.list_facts(limit=500)
    focus_path = folder / "meetingvar" / "agent_memory" / "focus.json"
    focus = []
    focus_sources = []
    focus_updated = ""
    if focus_path.exists():
        try:
            data = _json.loads(focus_path.read_text(encoding="utf-8"))
            focus = data.get("items", []) if isinstance(data, dict) else []
            focus_sources = data.get("sources", []) if isinstance(data, dict) else []
            focus_updated = data.get("updated_at", "") if isinstance(data, dict) else ""
        except (OSError, ValueError):
            focus = []
    counts = {}
    for fact in facts:
        kind = fact.get("metadata", {}).get("type", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {"ok": True, "folder": str(folder), "facts": len(facts), "by_type": counts,
            "focus": focus, "focus_sources": focus_sources, "focus_updated": focus_updated,
            "memory_index": str(store.index_path)}


def cmd_agent_focus_preview(folder: Path) -> dict:
    from meetingvar.agent.focus import FocusAgent

    agent = FocusAgent(project_root=folder, dry_run=True, approval=lambda items: True)
    return agent.preview()


def cmd_agent_focus_save(folder: Path, payload_file: Path) -> dict:
    import json as _json

    from meetingvar.agent.focus import FocusAgent

    try:
        payload = _json.loads(payload_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "关注清单 JSON 读取失败"}
    items = payload.get("items") if isinstance(payload, dict) else payload
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "关注清单为空"}
    agent = FocusAgent(project_root=folder, dry_run=False, approval=lambda items: True)
    return agent.save_items(items, sources if isinstance(sources, list) else [])


def cmd_agent_extract_preview(session: str, folder: Path) -> dict:
    from meetingvar import config
    from meetingvar.agent.postmeeting import PostMeetingAgent, load_transcript

    path = config.POOL_DIR / session / "transcript.json"
    if not path.exists():
        return {"ok": False, "error": "该会话没有保存的转写"}
    try:
        transcript = load_transcript(path)
    except (OSError, ValueError):
        return {"ok": False, "error": "转写文件不可读"}
    return PostMeetingAgent(session_id=session, transcript=transcript, project_root=folder,
                            dry_run=True, approval=lambda item, evidence: True).preview()


def cmd_agent_facts_save(session: str, folder: Path, payload_file: Path) -> dict:
    import json as _json

    from meetingvar import config
    from meetingvar.agent.postmeeting import PostMeetingAgent, load_transcript

    path = config.POOL_DIR / session / "transcript.json"
    if not path.exists():
        return {"ok": False, "error": "该会话没有保存的转写"}
    try:
        items = _json.loads(payload_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "事实 JSON 读取失败"}
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "没有选择任何事实"}
    try:
        transcript = load_transcript(path)
    except (OSError, ValueError):
        return {"ok": False, "error": "转写文件不可读"}
    return PostMeetingAgent(session_id=session, transcript=transcript, project_root=folder,
                            dry_run=False, approval=lambda item, evidence: True).save_items(items)


def cmd_agent_ask(folder: Path, question: str) -> dict:
    from meetingvar.agent.recall import MemoryQAAgent

    return MemoryQAAgent(project_root=folder, question=question).run()


def cmd_agent_run(folder: Path, question: str, max_steps: int = 8,
                  mode: str = "auto") -> dict:
    from meetingvar.agent.loop import AgentLoop

    loop = AgentLoop(project_root=folder, question=question, mode=mode,
                     max_steps=max(1, min(20, int(max_steps))))
    return loop.run()


def cmd_agent_resume(folder: Path, task_id: str, max_steps: int = 8,
                     mode: str = "auto") -> dict:
    from meetingvar.agent.loop import AgentLoop

    loop = AgentLoop(project_root=folder, question="(resume)", mode=mode,
                     max_steps=max(1, min(20, int(max_steps))))
    return loop.run(resume_task_id=task_id)


def cmd_agent_memory(folder: Path, query: str = "") -> dict:
    from meetingvar.agent.memory import FactStore

    store = FactStore(folder)
    facts = store.search(query, limit=50) if query.strip() else store.list_facts(limit=50)
    return {"ok": True, "facts": [{
        "name": fact.get("name", ""), "description": fact.get("description", ""),
        "type": fact.get("metadata", {}).get("type", ""),
        "origin_meeting": fact.get("metadata", {}).get("origin_meeting", ""),
        "updated_at": fact.get("metadata", {}).get("updated_at", ""),
        "body": str(fact.get("body", ""))[:1200],
    } for fact in facts]}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: engine_bridge.py preview <folder> | check"}))
        return
    cmd = sys.argv[1]
    try:
        if cmd == "preview":
            payload = cmd_preview(Path(sys.argv[2]))
        elif cmd == "check":
            payload = cmd_check()
        elif cmd == "check_xfyun":
            payload = cmd_check_xfyun(Path(sys.argv[2]))
        elif cmd == "transcribe_file":
            media = Path(sys.argv[2])
            xfyun_dir = Path(sys.argv[3])
            out_path = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None
            payload = cmd_transcribe_file(media, xfyun_dir, out_path)
        elif cmd == "test_llm":
            payload = cmd_test_llm()
        elif cmd == "understand_plan":
            payload = cmd_understand_plan(Path(sys.argv[2]))
        elif cmd == "understand":
            payload = cmd_understand(Path(sys.argv[2]),
                                     int(sys.argv[3]) if len(sys.argv) > 3 else 0)
        elif cmd == "write_terms":
            payload = cmd_write_terms(Path(sys.argv[2]), Path(sys.argv[3]))
        elif cmd == "agent_status":
            payload = cmd_agent_status(Path(sys.argv[2]))
        elif cmd == "agent_focus_preview":
            payload = cmd_agent_focus_preview(Path(sys.argv[2]))
        elif cmd == "agent_focus_save":
            payload = cmd_agent_focus_save(Path(sys.argv[2]), Path(sys.argv[3]))
        elif cmd == "agent_extract_preview":
            payload = cmd_agent_extract_preview(sys.argv[2], Path(sys.argv[3]))
        elif cmd == "agent_facts_save":
            payload = cmd_agent_facts_save(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
        elif cmd == "agent_ask":
            payload = cmd_agent_ask(Path(sys.argv[2]), sys.argv[3])
        elif cmd == "agent_run":
            payload = cmd_agent_run(Path(sys.argv[2]), sys.argv[3],
                                    int(sys.argv[4]) if len(sys.argv) > 4 else 8,
                                    sys.argv[5] if len(sys.argv) > 5 else "auto")
        elif cmd == "agent_resume":
            payload = cmd_agent_resume(Path(sys.argv[2]), sys.argv[3],
                                       int(sys.argv[4]) if len(sys.argv) > 4 else 8,
                                       sys.argv[5] if len(sys.argv) > 5 else "auto")
        elif cmd == "agent_memory":
            payload = cmd_agent_memory(Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")
        else:
            payload = {"ok": False, "error": f"unknown command: {cmd}"}
    except Exception as exc:  # noqa: BLE001
        if cmd.startswith("agent_") or cmd in {"test_llm", "understand", "understand_plan"}:
            payload = {"ok": False, "error_code": "internal_error", "complete": False,
                       "warnings": [], "error": "Agent/模型任务未能完成；内部异常详情未向输出公开"}
        else:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
