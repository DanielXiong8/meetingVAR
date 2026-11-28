---
name: runtime-probe
description: |
  运行环境探测。在 CodeArts / OfficeAce 的 Skill 运行时里执行一次，
  报告 Python 版本、ffmpeg/ffprobe、faster-whisper、PyAV、临时目录写入、
  子进程和 SHA256 是否可用。只读探测，不联网、不读密钥。
  Triggers include: "环境探测", "运行环境检查", "runtime probe",
  "检查 CodeArts 能不能跑 Python", "skill runtime diagnostics",
  "probe the skill runtime".
tags: [runtime, diagnostics, probe, codearts]
---

# 运行环境探测（Runtime Probe）

## Overview

这个 Skill 不处理业务数据，只回答一个问题：

> 当前 Skill 运行时到底能跑什么？

输出一份 JSON，用来决定两个参赛 Skill 走“完整版”还是“纯转写版”。

## Prerequisites

- Python 3.10+；
- 不需要网络、不需要第三方包、不需要任何密钥。

## Workflow

```text
1. 在 CodeArts / OfficeAce 中加载本 Skill
2. 运行 python scripts/probe.py
3. 把 JSON 保存为 evidence/runtime-probe.json
4. 按下面的判定表决定 Go / No-Go
```

## Core Commands

```bash
python scripts/probe.py
```

把结果保存下来：

```bash
python scripts/probe.py > runtime-probe.json
```

## Parameter Confirmation

无参数。运行前确认：

- 当前目录可写（脚本会在系统临时目录做一次写入测试）；
- 允许执行子进程（脚本会调用 `sys.executable -c` 做一次自检）。

如果运行环境禁止子进程，脚本仍会输出结果，`subprocess.ok` 会是 `false`。

## How to Read the Result

| 字段 | 含义 | 对我们的影响 |
|---|---|---|
| `python.version` | 运行时 Python 版本 | 需要 3.10+ |
| `ffmpeg.found` | 是否有 ffmpeg CLI | 决定能否生成 `clip_core.mp4` / `clip_full.mp4` |
| `modules.faster_whisper.ok` | 能否本地转写 | 决定能否从音视频直接生成转写 |
| `modules.av.ok` | PyAV 是否可用 | faster-whisper 解码依赖 |
| `write.ok` | 临时目录可写 | 证据包能否落盘 |
| `subprocess.ok` | 能否调用子进程 | 能否截图/截取片段 |
| `hashlib.ok` | SHA256 正常 | 证据包校验是否可信 |

### 判定表

| 情况 | 结论 |
|---|---|
| Python 3.10+ 且 write/hash 正常 | **Go**：核心定位功能可用 |
| 再加 ffmpeg | 完整版：可生成 clip 和关键帧 |
| 再加 faster-whisper + av | 可以从音视频直接转写 |
| Python 不可用 | **No-Go**：两个 Skill 都无法运行，需要换实现方式 |
| Python 可用但无 ffmpeg | 纯转写版：定位和证据包可用，无 clip |

## Reference Documents

- [reading-the-result.md](references/reading-the-result.md)：字段解释和常见异常。

## Example Output

```json
{
  "python": {"version": "3.10.21", "implementation": "CPython"},
  "ffmpeg": {"found": false},
  "modules": {
    "faster_whisper": {"ok": true, "version": "1.2.1"},
    "av": {"ok": true, "version": "14.0.0"}
  },
  "write": {"ok": true},
  "subprocess": {"ok": true, "returncode": 0},
  "hashlib": {"ok": true}
}
```