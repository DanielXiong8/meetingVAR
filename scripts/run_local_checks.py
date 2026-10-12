"""Run repeatable local verification; artifacts stay under ignored data/."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-simulation", action="store_true", help="仅运行回归与编译（最小依赖环境）")
    parser.add_argument("--isolated-cwd", action="store_true", help="在不含 .env 的独立目录运行，验证最小安装")
    parser.add_argument("--with-model-audits", action="store_true", help="另跑真实模型审计；缺模型/素材时会明确跳过")
    args = parser.parse_args()
    output = ROOT / "data" / "verification" / f"run_{time.time_ns()}"
    output.mkdir(parents=True)
    temporary = output / "tmp"
    temporary.mkdir()
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "TEMP": str(temporary), "TMP": str(temporary),
                "PYTHONPATH": str(ROOT / "src"), "MEETINGVAR_LLM_BASE_URL": "",
                "MEETINGVAR_LLM_MODEL": "", "MEETINGVAR_LLM_API_KEY": "",
                "MEETINGVAR_LLM_THINKING": "", "MEETINGVAR_LLM_MAX_TOKENS": "2048",
                "MEETINGVAR_CHUNK": "8", "MEETINGVAR_LOOKBACK": "300",
                "MEETINGVAR_MIN_TEXT_CHARS": "2", "NO_PROXY": "127.0.0.1,localhost"})
    cwd = output if args.isolated_cwd else ROOT
    checks = [
        ("unittest", ["-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"]),
        ("compile", ["-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests")]),
    ]
    if not args.skip_simulation:
        checks.append(("simulation", ["-m", "meetingvar.simulation", "--output", str(output / "simulation")]))
    if args.with_model_audits:
        for name in ("audit_zero_persistence", "timing_calibration"):
            checks.append((name, [str(ROOT / "tests" / (name + ".py")), "-v"]))
    versions = {}
    for package in ("numpy", "jieba", "scikit-learn", "pypinyin", "pillow", "cryptography",
                    "python-dotenv", "funasr", "torch", "PyAudioWPatch", "dxcam", "mss"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    result = {"python": sys.version.split()[0], "executable": sys.executable,
              "output": str(output), "packages": versions, "checks": [],
              "simulation_skipped": args.skip_simulation,
              "boundaries": ["ASR model and capture sources are simulated",
                             "no cloud LLM requests", "real meetings/GPU are not certified"]}
    for name, command in checks:
        print(f"[check] {name}", flush=True)
        start = time.monotonic()
        process = subprocess.run([sys.executable, *command], cwd=cwd, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = process.stdout.decode("utf-8", "replace")
        log_path = output / f"{name}.log"
        log_path.write_text(log, encoding="utf-8")
        counts = re.search(r"Ran (\d+) tests?", log)
        skipped = re.search(r"skipped=(\d+)", log)
        check = {"name": name, "exit_code": process.returncode, "log": str(log_path),
                 "seconds": round(time.monotonic() - start, 2)}
        if counts:
            check.update(tests=int(counts[1]), skipped=int(skipped[1]) if skipped else 0)
        check["status"] = "failed" if process.returncode else (
            "skipped" if check.get("tests", 0) and check.get("skipped") == check["tests"] else "passed")
        result["checks"].append(check)
        print(f"[{check['status']}] {name}: {log_path}", flush=True)
        if process.returncode:
            print("\n".join(log.splitlines()[-35:]), flush=True)
    result["status"] = "passed" if all(check["exit_code"] == 0 for check in result["checks"]) else "failed"
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
