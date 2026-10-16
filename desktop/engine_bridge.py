"""引擎侧只读预览桥：由桌面壳用"引擎 Python"调用，stdout 只输出一行 JSON。

职责（全部只读，不写任何文件、不触发保存链路）：
- preview <folder>：扫描项目文件夹 → 文档清单 / 抽取词表（L1 jieba）/ 仓库符号（可选）/ 人物名单；
- check：引擎环境自检。

用法（由 ui 后端自动拼命令，勿手工改路径）：
    <engine_python> engine_bridge.py preview "D:\\项目\\xxx"
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
        "iat": None,
        "lfasr": None,
        "files": found["files"],
        "notes": [],
    }
    if found["iat"]:
        probe = {"valid": False}
        try:
            from meetingvar.transcribe.xfyun_iat import load_creds

            load_creds(Path(found["iat"]))
            probe = {"valid": True}
        except Exception as exc:  # noqa: BLE001
            probe = {"valid": False, "error": str(exc)[:120]}
        if probe["valid"]:
            probe.update(_probe_iat(Path(found["iat"])))  # 联网探活（静音握手）
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
        else:
            payload = {"ok": False, "error": f"unknown command: {cmd}"}
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
