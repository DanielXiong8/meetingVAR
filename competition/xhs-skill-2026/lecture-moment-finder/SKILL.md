---
name: lecture-moment-finder
description: |
  课堂实时定位 + 课后回放。上课前写下你关心的问题，启动后自动录音/录屏、
  实时转写；命中后持续跟踪，直到老师讲完这个知识点，自动保存完整区间
  的音频、屏幕录像和原话（例如 01:30–06:30 全程）；课后也可以对整段转写
  做“老师讲 XX 的那几分钟”的定位。纯本地运行，不联网、不上传录音。
  Triggers include: "开始上课/开始记录", "老师讲……的那几分钟", "实时定位课堂重点",
  "录音里找老师讲……", "课堂回放定位", "lecture moment finder",
  "start a live lecture capture", "find the minute my teacher covered",
  以及用户给出课堂录音/转写并询问“讲到哪一段/哪几分钟/哪里提到”时。
tags: [education, lecture, live-capture, find-moment, replay, offline, xiaohongshu-skill]
---

# 课堂实时定位 + 回放（Lecture Moment Finder）

## Overview

两种用法，共用一个定位内核：

```text
实时模式（上课中）
  录音/录屏 + 课前关心清单 → 实时转写 → 命中 → 跟踪到主题结束
  → 完整区间的音频 + 屏幕录像 + 原话

课后模式（下课后）
  完整转写 + 一句问题 → 自适应完整区间 + 核心 30–90 秒 → 结果与回放材料
```

它不做会议纪要，也不生成课程总结。它只做一件事：**在你关心的地方留下可回放的完整片段**。

适用场景：

- 上课：老师讲到“考试范围 / 某个例题 / 某个公式”时，自动保存那一段；
- 补课：网课太长，先跳到没听懂的那一段；
- 教研/培训：从多段录课里找到某个知识点首次出现的位置；
- 内容二创：从长录屏里定位可以剪成短视频的片段。

## Prerequisites

- **Python 3.10–3.12**：实时模式需要 3.10+；3.13 移除了 `audioop`，不支持。
- **课后模式**：纯标准库，无需安装任何第三方包。
- **实时模式**：首次运行装依赖：
  `python scripts/ensure_deps.py --model base [--mirror]`，
  会安装 `pyaudiowpatch`、`mss`、`Pillow`、`numpy`、`faster-whisper`、`av`
  （`av`/PyAV 负责屏幕录像，不需要外部 ffmpeg）。
- **Windows**：系统声音环回依赖 WASAPI；线下课可改用麦克风。
- **可选**：本机 `ffmpeg`，用于从音视频截取片段和关键帧。

## Workflow

1. **课前**：复制 `examples/interests.example.json`，写下关心的问题和关键词。
2. **启动**：`live_ctl.py doctor` → `devices` → `start --consent`。
3. **上课**：`watch` 看命中；命中后引擎继续跟踪，直到这个知识点讲完。
4. **下课**：`stop`；在 `hits/hit_NNN/` 拿到完整区间音频、屏幕录像和原话。
5. **复习**：直接回放命中的 `audio.wav`，或用 `find_moment.py` 对完整转写做二次定位。

## Core Commands

```bash
python scripts/live_ctl.py doctor                 # 依赖 + 设备检查
python scripts/live_ctl.py devices                # 列出音频设备/显示器
python scripts/live_ctl.py start --config ... --out ... --consent
python scripts/live_ctl.py watch --out ...        # 看命中（不停止引擎）
python scripts/live_ctl.py stop --out ...         # 优雅停止
python scripts/live_ctl.py simulate --config ... --transcript ... --out ... --speed 0
python scripts/find_moment.py --transcript ... --query "老师讲 XX 的那几分钟"
python scripts/selftest.py                        # 离线 + 实时完整区间自测
```

## Live Mode（上课中，主模式）

### 它怎么工作

```text
课前：interests.json（你关心的问题 + 关键词）
  ↓
启动：录音（系统声音/麦克风）+ 录屏（可选）
  ↓
实时：每 3 秒看最近 10 秒，本地 faster-whisper 转写
  ↓
匹配：关键词 + 短语加权，超过阈值且不在冷却期 → 命中
  ↓
跟踪：命中后不立刻截断；主题还在讲就继续录
  ↓
收尾：主题结束（相关度下降/停顿/下课）或达到上限 → 保存完整区间
  ↓
保存：完整区间的音频 + 屏幕录像 + 转写 + 原话 → hits/hit_NNN/
  ↓
课后：可继续用完整转写做二次定位
```

### 课前：写关心清单

复制 `examples/interests.example.json`，改 `interests`：

```json
{
  "session_id": "lecture-2026-11-17",
  "title": "数据结构 第 7 讲",
  "consent": false,
  "interests": [
    {
      "id": "dp",
      "question": "老师讲动态规划状态转移方程的那几分钟",
      "keywords": ["动态规划", "状态转移方程", "状态定义", "背包"],
      "min_score": 25.0,
      "min_keyword_hits": 1,
      "consecutive_hits": 1,
      "cooldown_seconds": 180
    }
  ],
  "capture": {
    "source": "loopback",
    "screen": true,
    "fps": 1.0,
    "video_fps": 5.0,
    "video_width": 1280,
    "video_crf": 28,
    "monitor": 1
  },
  "asr": {
    "model": "base",
    "language": "zh",
    "chunk_seconds": 10.0,
    "step_seconds": 3.0,
    "vad_filter": true
  },
  "trigger": {
    "window_seconds": 20.0,
    "ring_seconds": 300.0,
    "min_span": 45.0,
    "max_span": 600.0,
    "end_grace_seconds": 60.0,
    "rel_threshold": 0.15,
    "bridge": 2,
    "max_gap": 20.0
  }
}
```

`trigger` 的含义：

| 字段 | 默认 | 作用 |
|---|---|---|
| `window_seconds` | 20 | 匹配时回看多少秒转写 |
| `ring_seconds` | 300 | 音频/屏幕环形缓冲；决定命中时最多能向前回溯多久 |
| `min_span` | 45 | 命中区间最短多少秒 |
| `max_span` | 600 | 单个区间最长多少秒；超长知识点会自动分段保存 |
| `end_grace_seconds` | 60 | 主题结束后再等多久收尾（给 ASR 延迟留余量） |
| `rel_threshold` | 0.15 | 后一句相关度低于峰值的这个比例，就认为主题结束了 |
| `bridge` | 2 | 允许跨过几个低相关句继续跟踪 |
| `max_gap` | 20 | 相邻两句间隔超过多少秒视为停顿/换话题 |

`capture.source`：

| 值 | 用途 |
|---|---|
| `loopback` | 线上课/录屏播放，抓系统声音（推荐） |
| `mic` | 线下课堂，抓麦克风 |
| `both` | 线上+线下混合；两路混音 |

### 启动前：检查环境和设备

```bash
# 依赖是否齐全（pyaudiowpatch / mss / Pillow / numpy / faster-whisper）
python scripts/live_ctl.py doctor

# 列出音频设备和显示器
python scripts/live_ctl.py devices
```

如果 `doctor` 显示缺依赖，用 meetingVAR 的引擎环境运行：

```bash
"D:\miniconda3\envs\meetingvar\python.exe" scripts/live_ctl.py doctor
```

### 启动 / 查看 / 停止

```bash
# 启动（必须确认已获得录音/录屏许可）
python scripts/live_ctl.py start \
  --config examples/interests.example.json \
  --out out/lecture-2026-11-17 \
  --consent

# 查看状态
python scripts/live_ctl.py status --out out/lecture-2026-11-17

# 持续看新命中（Ctrl+C 只退出监听，不停止引擎）
python scripts/live_ctl.py watch --out out/lecture-2026-11-17

# 优雅停止
python scripts/live_ctl.py stop --out out/lecture-2026-11-17
```

如果 CodeArts 的 Python 没有采集依赖，用 `--python` 指定引擎环境：

```bash
python scripts/live_ctl.py start \
  --config examples/interests.example.json \
  --out out/lecture-2026-11-17 \
  --consent \
  --python "D:\miniconda3\envs\meetingVAR\python.exe"
```

> 本机如果存在 `D:\miniconda3\envs\meetingvar\python.exe`，`live_ctl.py start` 会自动使用它，
> 不需要每次传 `--python`。

### 实时模式输出

```text
out/lecture-2026-11-17/
├── status.json          # 当前状态、最近一句、已完成/正在抓取的命中
├── transcript.jsonl     # 实时转写（ts/end/text）
├── hits.jsonl           # 命中事件流（hit_open / hit_close）
├── session.json         # 课后汇总：配置、耗时、文件大小
├── engine.log           # 后台引擎日志
└── hits/
    └── hit_001/
        ├── audio.wav    # 完整知识点区间（例如 01:30–06:30）
        ├── screen.mp4   # 同一区间的屏幕录像（5fps / 1280 宽）
        ├── transcript.txt
        └── hit.json     # 问题、原话、完整区间、核心片段、结束原因
```

> 屏幕录像用 PyAV 在本机编码，不依赖外部 ffmpeg；如果 PyAV 不可用，会自动回退为
> `screens/` 关键帧截图。学习 Skill 只保存**可回放材料**，不生成
> `manifest.json` / `SHA256SUMS.txt` / zip；需要文件完整性哈希时，用法律取证 Skill
> （`recording-evidence-finder`）。

### 没有设备时：回放转写

用一段转写模拟“上课过程”，不打开麦克风/屏幕，适合演示和 QA：

```bash
python scripts/live_ctl.py simulate \
  --config examples/interests.example.json \
  --transcript examples/lecture-transcript.json \
  --out out/simulated-lecture \
  --speed 10
```

`--speed 0` 表示立即跑完；`--speed 10` 表示 10 倍速。

### 调参

| 现象 | 调整 |
|---|---|
| 命中太频繁 | 提高 `min_score`、`consecutive_hits`、`cooldown_seconds` |
| 总是漏掉 | 降低 `min_score`、补充 `keywords`、降低 `min_keyword_hits` |
| 转写跟不上 | 用 `model: "tiny"`，或增大 `step_seconds` |
| 转写太碎 | 增大 `chunk_seconds`，或换 `model: "small"` |
| 区间太短/提前收尾 | 提高 `end_grace_seconds`、降低 `rel_threshold`、提高 `max_gap` |
| 区间太长 | 降低 `end_grace_seconds`、降低 `max_span` |
| 一个知识点被拆成多段 | 提高 `max_span`、提高 `end_grace_seconds` |
| 录屏文件太大 | 降低 `video_fps` / `video_width`，或提高 `video_crf` |
| 不想存屏幕 | `capture.screen: false` |
| 只想看命中、不存全过程 | `save_transcript: false`（按需） |

### 实时模式的边界

- **Windows 优先**：系统声音环回依赖 WASAPI；macOS/Linux 需要换采集实现。
- **本地模型**：默认 `base`，CPU 可跑；模型越大越准也越慢。
- **转写不是逐字稿**：分块 ASR 会在句首/句尾有少量误差，命中片段以原话+音频为准。
- **不做说话人分离**：在线课通常只有老师一路声音；需要区分说话人时再扩展。
- **不做语义判断**：命中是关键词+短语加权，不是大模型“理解”。问题要具体。
- **屏幕录像**：默认 5fps、1280 宽，用于回放定位；没有 PyAV 时自动回退为关键帧截图。

## Post-class Mode（下课后，回放定位）

### 定位并生成回放材料

```bash
python scripts/find_moment.py \
  --transcript examples/lecture-transcript.json \
  --query "老师讲动态规划状态转移方程的那几分钟" \
  --out out/dp-moment
```

有原始录屏时，加上 `--media`，会额外生成核心片段、完整讨论片段和关键帧：

```bash
python scripts/find_moment.py \
  --transcript path/to/lecture.srt \
  --query "老师讲考试范围的那几分钟" \
  --media path/to/lecture.mp4 \
  --out out/exam-scope
```

只查看结果、不写文件：

```bash
python scripts/find_moment.py \
  --transcript path/to/lecture.srt \
  --query "老师讲二叉树遍历" \
  --no-save
```

### 窗口机制

**完整讨论区间不是固定 30–90 秒。** 从命中句出发，按相邻句的相关度继续向两边扩展：

- 相邻句仍然在讲同一主题 → 继续扩展；
- 遇到长静音（默认 20 秒）、课间休息、相关度明显下降 → 停止；
- 上限按录音长度自动计算（默认 30%，最多 10 分钟），避免扩到整场课。

完整区间内部再挑**最密的 30–90 秒**作为核心片段。这样既能看到“老师从哪讲到哪”，
又能一键回放最关键的那几十秒。

### 输出文件

```text
out/dp-moment/
├── result.json             # 完整区间 + 核心片段 + 命中理由
├── report.md               # 可读报告
├── transcript_excerpt.txt  # 命中区间的原话
├── clip_core.mp4           # 有 --media 且本机有 ffmpeg 时才生成
├── clip_full.mp4
└── frames/                 # 核心片段的关键帧
```

不生成 `manifest.json` / `SHA256SUMS.txt` / zip；学习 Skill 只负责定位和回放。

### 可选：本地生成转写

```bash
python scripts/transcribe.py path/to/lecture.mp4 --out lecture.json --model small
```

需要 `ffmpeg` 和 `faster-whisper`。CPU 可跑；模型越大越准也越慢。

## Parameter Confirmation

### 实时模式

| 参数/字段 | 必填 | 说明 |
|---|---|---|
| `--config` | 是 | `interests.json` 路径 |
| `--out` | 是 | 会话输出目录 |
| `--consent` | 是 | 确认已获得录音/录屏许可 |
| `--python` | 否 | 有采集依赖的 Python；缺省用当前解释器 |
| `interests[].question` | 是 | 一句具体问题 |
| `interests[].keywords` | 建议 | 命中的硬条件，至少 1 个 |
| `interests[].min_score` | 否 | 分数阈值，默认 25 |
| `capture.source` | 否 | `loopback` / `mic` / `both` |
| `capture.screen` | 否 | 是否录屏 |
| `capture.video_fps` | 否 | 录屏帧率，默认 5 |
| `capture.video_width` | 否 | 录屏宽度，默认 1280 |
| `capture.video_crf` | 否 | 录屏质量（CRF 越大文件越小），默认 28 |
| `capture.monitor` | 否 | 录制哪个显示器，默认 1（主屏） |
| `asr.model` | 否 | `tiny` / `base` / `small` / `none` |
| `trigger.ring_seconds` | 否 | 音频/屏幕缓冲秒数，默认 300 |
| `trigger.max_span` | 否 | 单个区间上限，默认 600 秒 |
| `trigger.end_grace_seconds` | 否 | 主题结束后收尾等待，默认 60 秒 |

### 课后模式

| 参数 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `--transcript` | 是 | 带时间戳的转写文件 | `lecture.srt` |
| `--query` | 是 | 一个具体知识点 | `老师讲状态转移方程` |
| `--out` | 否 | 输出目录，默认 `./out` | `out/dp` |
| `--media` | 否 | 原始音视频；有才会生成 clip | `lecture.mp4` |
| `--min-span` / `--max-span` | 否 | 完整讨论区间；`max-span=0` 自动 | `45` / `0` |
| `--core-min` / `--core-max` | 否 | 核心片段秒数，默认 30–90 | `30` / `90` |
| `--no-save` | 否 | 只打印结果，不写文件 | — |

如果用户没有转写、也暂时不想装 `faster-whisper`，直接告诉他：
“先导出字幕（SRT/VTT）也可以，效果一样。”

## Privacy and Limits

- **本地运行**：实时采集、转写、匹配、命中片段都在本机完成，不上传。
- **必须确认许可**：`start` 必须带 `--consent`，或在 config 里 `consent: true`。
- **只处理用户自己的课堂**：不抓取、不下载他人课程。
- **可回放**：时间戳、原话、完整区间音频都在 `hits/hit_NNN/` 里，结论可以人工核对。
- **不做哈希校验**：学习 Skill 不生成 manifest/SHA256SUMS；需要完整性校验时用法律取证 Skill。
- **不做笔记总结**：它只负责“找到并保存那一段”，总结交给用户或别的工具。
- **不是法律/合规工具**：课堂录音的使用请遵守学校规定和当地法律。

## Reference Documents

- [live-mode.md](references/live-mode.md)：实时模式配置、输出、调参、故障排查。
- [output-format.md](references/output-format.md)：实时 `hit.json` 与课后 `result.json` 字段。
- [asr-setup.md](references/asr-setup.md)：转写模型与字幕格式。
- [acceptance-criteria.md](references/acceptance-criteria.md)：比赛提交规范与自检清单。
- [privacy-and-consent.md](references/privacy-and-consent.md)：录音来源、同意与使用边界。

## Examples

### 实时模式：3 步

```bash
python scripts/live_ctl.py doctor
python scripts/live_ctl.py start --config examples/interests.example.json --out out/lecture --consent
python scripts/live_ctl.py watch --out out/lecture
```

命中后：

```text
[命中开始] dp @ 1320.5s 从 01:30 开始，继续抓取完整知识点区间…
  今天我们来学习动态规划的状态转移方程。
[命中完成] dp 01:30 – 06:30（300 秒）
  状态转移方程是动态规划的核心。
  命中目录：out/lecture/hits/hit_001
```

### 课后模式：定位动态规划重点

```bash
python scripts/find_moment.py \
  --transcript examples/lecture-transcript.json \
  --query "老师讲动态规划状态转移方程的那几分钟"
```

预期：完整讨论 `40:00 – 44:48`，核心片段 `41:20 – 42:28`。

### 自测

```bash
python scripts/selftest.py
```

预期输出 `SELFTEST PASS`。

### 打包提交

```bash
python scripts/package.py . --out ../submission/lecture-moment-finder.zip
```

会检查文件数 ≤30、总大小 ≤40MB、`SKILL.md` ≤500 行、扩展名合规，
并自动排除 `__pycache__`、`out/` 等运行产物。