#!/usr/bin/env python3
"""Build a submission zip and check the Huawei Cloud skill limits.

Checks: <=30 files, <=40MB, SKILL.md <=500 lines, allowed extensions only.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ALLOWED = set(
    "md mdx txt json json5 yaml yml toml js cjs mjs ts tsx jsx py sh ps1 psm1 "
    "psd1 r rb go rs swift kt java cs cpp c h hpp sql csv tsv ini cfg conf env "
    "properties dat xml html css scss sass svg".split()
)
EXCLUDED_DIRS = {"__pycache__", ".git", "dist", "out"}


def collect(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() == ".pyc":
            continue
        files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包并检查 Skill 提交限制。")
    parser.add_argument("skill_dir", nargs="?", default=".",
                        help="Skill 目录（默认当前目录）")
    parser.add_argument("--out", default="", help="输出 zip 路径")
    args = parser.parse_args(argv)

    root = Path(args.skill_dir).resolve()
    if not (root / "SKILL.md").is_file():
        print(f"缺少 SKILL.md：{root}", file=sys.stderr)
        return 2

    files = collect(root)
    total = sum(path.stat().st_size for path in files)
    lines = len((root / "SKILL.md").read_text(encoding="utf-8").splitlines())
    invalid = [
        str(path.relative_to(root)) for path in files
        if path.suffix.lower().lstrip(".") not in ALLOWED
    ]

    problems = []
    if len(files) > 30:
        problems.append(f"文件数 {len(files)} > 30")
    if total > 40 * 1024 * 1024:
        problems.append(f"总大小 {total / 1048576:.1f}MB > 40MB")
    if lines > 500:
        problems.append(f"SKILL.md {lines} 行 > 500")
    if invalid:
        problems.append("扩展名不在允许列表：" + ", ".join(invalid))

    print(f"Skill: {root.name}")
    print(f"文件数: {len(files)} / 30")
    print(f"总大小: {total / 1024:.1f} KB / 40MB")
    print(f"SKILL.md: {lines} 行 / 500")
    if problems:
        print("FAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    out = Path(args.out) if args.out else root.parent / "submission" / f"{root.name}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())

    print(f"PASS: {out}")
    print(f"zip 大小: {out.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())