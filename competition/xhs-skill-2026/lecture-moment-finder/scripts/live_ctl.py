#!/usr/bin/env python3
"""Control the live lecture engine: devices / start / status / watch / stop."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_core import list_devices, load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).resolve().parent / "live_engine.py"
DEFAULT_PYTHON = Path(r"D:\miniconda3\envs\meetingvar\python.exe")
ACTIVE_STATES = {"starting", "loading_asr", "recording"}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _print_status(out: Path) -> dict:
    status = _read_json(out / "status.json")
    if not status:
        print(f"没有 status.json：{out}")
        return {}
    print(f"状态：{status.get('state', '?')}")
    print(f"会话：{status.get('session_id', '')}")
    print(f"已运行：{status.get('elapsed', 0)} 秒")
    print(f"已完成命中：{status.get('hits', 0)} 次")
    print(f"转写行数：{status.get('transcript_lines', 0)}")
    if status.get("last_text"):
        print(f"最近一句：{status['last_text'][:160]}")
    for hit in status.get("open_hits") or []:
        print(f"正在抓取：[{hit.get('interest_id')}] 从 {hit.get('span_start_text')} 开始"
              f"（已 {hit.get('duration')} 秒；主题结束后自动收尾）")
    if status.get("last_hit"):
        hit = status["last_hit"]
        print(f"最近命中：[{hit.get('interest_id')}] "
              f"{hit.get('span_start_text')} – {hit.get('span_end_text')}"
              f"（{hit.get('duration')} 秒）")
        if hit.get("key_quote"):
            print(f"  原话：{hit['key_quote'][:120]}")
        print(f"  命中目录：{hit.get('dir', '')}")
    if status.get("error"):
        print(f"错误：{status['error']}")
    return status


def cmd_devices(_args) -> int:
    print(json.dumps(list_devices(), ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(_args) -> int:
    print(f"python: {sys.executable} ({sys.version.split()[0]})")
    missing: list[str] = []
    for module in ("pyaudiowpatch", "mss", "PIL", "numpy", "faster_whisper", "av"):
        try:
            imported = __import__(module)
            print(f"{module}: OK {getattr(imported, '__version__', '')}")
        except Exception as exc:  # noqa: BLE001
            missing.append(module)
            print(f"{module}: MISSING ({type(exc).__name__}: {exc})")
    if missing:
        print("\n缺少实时模式依赖，运行一次引导：")
        print(f"  {sys.executable} scripts/ensure_deps.py --model base [--mirror]")
    else:
        print("\n实时依赖已齐全，可以开始。")
    print("--- devices ---")
    print(json.dumps(list_devices(), ensure_ascii=False, indent=2))
    return 0


def cmd_start(args) -> int:
    config_path = Path(args.config).resolve()
    out = Path(args.out).resolve()
    config = load_config(config_path)
    consent = bool(args.consent or config.get("consent"))
    if not consent:
        print("拒绝启动：未确认录音/录屏许可。请加 --consent，或在配置里设置 consent=true。",
              file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    status = _read_json(out / "status.json")
    if status.get("state") in ACTIVE_STATES and not args.force:
        print(f"已有会话在运行（{status.get('state')}）。先 stop，或加 --force。", file=sys.stderr)
        return 1
    stop_file = out / "STOP"
    if stop_file.exists():
        stop_file.unlink()

    python = (args.python or config.get("python")
              or (str(DEFAULT_PYTHON) if DEFAULT_PYTHON.is_file() else sys.executable))
    command = [
        python, str(ENGINE),
        "--config", str(config_path),
        "--out", str(out),
        "--consent",
    ]
    if args.duration:
        command += ["--duration", str(args.duration)]
    if args.foreground:
        print("前台运行，Ctrl+C 结束。")
        return subprocess.call(command, cwd=str(ROOT))

    log_path = out / "engine.log"
    log = open(log_path, "ab")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=flags,
        )
    finally:
        log.close()
    (out / "pid").write_text(str(process.pid), encoding="utf-8")
    print(f"已启动：pid={process.pid}")
    print(f"日志：{log_path}")
    time.sleep(1.5)
    _print_status(out)
    return 0


def cmd_status(args) -> int:
    status = _read_json(Path(args.out) / "status.json")
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        _print_status(Path(args.out))
    return 0


def cmd_watch(args) -> int:
    out = Path(args.out)
    hits_path = out / "hits.jsonl"
    seen = 0
    if hits_path.is_file():
        seen = len(hits_path.read_text(encoding="utf-8").splitlines())
    print(f"监听 {out}（Ctrl+C 退出）")
    try:
        while True:
            if hits_path.is_file():
                lines = hits_path.read_text(encoding="utf-8").splitlines()
                for line in lines[seen:]:
                    try:
                        hit = json.loads(line)
                    except ValueError:
                        continue
                    if hit.get("event") == "hit_open":
                        print(f"\n[命中开始] {hit.get('interest_id')} @ {hit.get('at')}s "
                              f"从 {hit.get('span_start_text')} 开始，继续抓取完整知识点区间…")
                        print(f"  {hit.get('quote', '')[:160]}")
                    else:
                        print(f"\n[命中完成] {hit.get('interest_id')} "
                              f"{hit.get('span_start_text')} – {hit.get('span_end_text')} "
                              f"（{hit.get('duration')} 秒）")
                        print(f"  {(hit.get('key_quote') or hit.get('quote', ''))[:160]}")
                        print(f"  命中目录：{hit.get('dir', '')}")
                seen = len(lines)
            status = _read_json(out / "status.json")
            if status.get("state") in {"stopped", "error"} and not args.follow:
                print(f"\n会话结束：{status.get('state')}（命中 {status.get('hits', 0)} 次）")
                if status.get("error"):
                    print(f"错误：{status['error']}")
                return 0
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        print("\n停止监听（引擎仍在运行）")
        return 0


def cmd_stop(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "STOP").write_text("stop", encoding="utf-8")
    print("已写入 STOP，等待引擎收尾...")
    deadline = time.time() + max(3.0, args.timeout)
    while time.time() < deadline:
        status = _read_json(out / "status.json")
        if status.get("state") in {"stopped", "error"}:
            _print_status(out)
            return 0
        time.sleep(0.5)
    print("引擎尚未停止；请稍后查看 status.json，或检查 engine.log。", file=sys.stderr)
    return 1


def cmd_simulate(args) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(ENGINE),
        "--config", str(Path(args.config).resolve()),
        "--out", str(out),
        "--consent",
        "--simulate", str(Path(args.transcript).resolve()),
        "--speed", str(args.speed),
    ]
    return subprocess.call(command, cwd=str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="课堂实时取证控制台。")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("devices", help="列出音频设备与显示器")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("doctor", help="检查 Python 依赖和设备")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("start", help="启动实时采集")
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--consent", action="store_true")
    p.add_argument("--python", default="", help="运行引擎的 Python（默认当前解释器）")
    p.add_argument("--foreground", action="store_true")
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status", help="查看会话状态")
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("watch", help="持续输出新命中")
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--follow", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("stop", help="优雅停止")
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=float, default=15.0)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("simulate", help="用转写文件回放，不打开设备")
    p.add_argument("--config", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--speed", type=float, default=10.0)
    p.set_defaults(func=cmd_simulate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())