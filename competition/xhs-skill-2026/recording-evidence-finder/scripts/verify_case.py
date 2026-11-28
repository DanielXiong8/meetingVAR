#!/usr/bin/env python3
"""Verify a recording-evidence pack (directory or zip)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_core import verify_pack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验录音证据包的 SHA256 完整性。")
    parser.add_argument("pack", help="证据包目录或 .zip 文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    try:
        result = verify_pack(args.pack)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"校验文件数：{result['checked']}")
        if result["ok"]:
            print("PASS：所有文件哈希一致。")
        else:
            print("FAIL：")
            for failure in result["failures"]:
                print(f"  - {failure}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())