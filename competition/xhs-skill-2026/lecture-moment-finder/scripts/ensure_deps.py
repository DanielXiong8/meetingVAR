#!/usr/bin/env python3
"""One-time bootstrap for live mode on a machine without meetingVAR.

Checks Python version, installs missing live-mode dependencies, and can
pre-download the faster-whisper model. Safe to run multiple times.

Usage:
    python scripts/ensure_deps.py                 # install missing, no model
    python scripts/ensure_deps.py --model base     # also download base model
    python scripts/ensure_deps.py --mirror          # use hf-mirror.com
    python scripts/ensure_deps.py --dry-run         # just report
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys

LIVE_MODULES = [
    ("pyaudiowpatch", "pyaudiowpatch"),
    ("mss", "mss"),
    ("PIL", "pillow"),
    ("numpy", "numpy"),
    ("faster_whisper", "faster-whisper"),
    ("av", "av"),
]
OPTIONAL_MODULES = [
    ("av", "av"),
]


def check_python() -> bool:
    major, minor = sys.version_info[:2]
    version = sys.version.split()[0]
    if (major, minor) < (3, 10):
        print(f"[FAIL] Python {version} 太旧；实时模式需要 3.10+。")
        return False
    if (major, minor) >= (3, 13):
        print(f"[FAIL] Python {version} 移除了 audioop；实时模式需要 3.10–3.12。")
        return False
    print(f"[ OK ] Python {version}（{sys.executable}）")
    return True


def missing_modules() -> list[str]:
    missing = []
    for module_name, package_name in LIVE_MODULES:
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return missing


def pip_install(packages: list[str], user: bool = False) -> bool:
    command = [sys.executable, "-m", "pip", "install", "--no-input"]
    if user:
        command.append("--user")
    command.extend(packages)
    print(f"运行：{' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode == 0


def download_model(model: str, mirror: bool) -> bool:
    if mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[FAIL] faster-whisper 不可用，无法下载模型。")
        return False
    print(f"下载/检查 faster-whisper 模型：{model}（首次需要联网）...")
    try:
        WhisperModel(model, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001 - report any download failure
        print(f"[FAIL] 模型下载失败：{type(exc).__name__}: {exc}")
        print("国内网络可以加 --mirror 用 hf-mirror.com；"
              "也可以从已装好的机器复制模型缓存目录。")
        return False
    print(f"[ OK ] 模型 {model} 已就绪")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实时模式依赖引导。")
    parser.add_argument("--model", default="",
                        help="预下载 faster-whisper 模型：tiny/base/small")
    parser.add_argument("--mirror", action="store_true",
                        help="模型下载走 hf-mirror.com")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检查不安装")
    parser.add_argument("--user", action="store_true",
                        help="pip install 使用 --user（没有管理员权限时）")
    args = parser.parse_args(argv)

    ok = check_python()
    missing = missing_modules()
    optional_missing = [
        pkg for mod, pkg in OPTIONAL_MODULES
        if importlib.util.find_spec(mod) is None
    ]

    if not missing:
        print("[ OK ] 实时模式依赖已齐全。")
    elif args.dry_run:
        print(f"[-- ] 缺少：{', '.join(missing)}（--dry-run，不安装）")
    else:
        print(f"安装：{', '.join(missing)} ...")
        if not pip_install(missing, user=False):
            print("常规安装失败，尝试 --user ...")
            if not pip_install(missing, user=True):
                print("[FAIL] pip 安装失败。需要管理员权限或虚拟环境。")
                ok = False
                missing = [m for m in missing if m not in optional_missing]
            else:
                missing = []
    if not args.dry_run and missing:
        ok = False

    if args.model and args.model in {"tiny", "base", "small"}:
        if ok and not args.dry_run:
            ok = download_model(args.model, args.mirror) and ok

    if ok:
        print("\n全部就绪。下一步：")
        print("  python scripts/live_ctl.py doctor")
        print("  python scripts/live_ctl.py devices")
        if not missing:
            print("  python scripts/live_ctl.py start --config ... --out ... --consent")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())