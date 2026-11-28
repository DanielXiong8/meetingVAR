---
name: recording-evidence-finder
description: |
  录音/录屏证据定位。输入一段录音或录屏的转写文本和一条事实主张，
  定位与主张最相关的完整语境，并给出核心 25–75 秒，输出时间戳、原话、说话人、
  前后文、可播放片段（有原始媒体时）和 SHA256 可校验证据包。
  纯本地运行，不上传录音；只做材料整理与辅助定位，不构成法律意见。
  Triggers include: "录音里找证据", "他什么时候承认……", "录屏里找……",
  "帮我在录音里定位……", "取证", "证据定位", "recording evidence finder",
  "find evidence in a recording", "locate an admission in audio",
  以及用户给出录音转写并询问“他有没有说过/哪一段说了/什么时候承认”时。
tags: [legal, evidence, recording, find-moment, offline, xiaohongshu-skill]
---

# 录音证据定位（Recording Evidence Finder）

## Overview

从几十分钟甚至几小时的录音/录屏里，找到与一条事实主张最相关的那几十秒：

```text
转写文本 + 一条主张  →  完整语境 + 核心证据 + 原话 + 说话人 + 哈希证据包
```

它适合：

- 商务纠纷：对方承认延期、承诺退款、改口的时间点；
- 劳动仲裁：录用条件、调岗、离职补偿的沟通原话；
- 客户投诉：客服承诺了什么、什么时候承诺的；
- 内部复盘：谁在什么时间点确认了哪个决定。

它**不做**：

- 不判断证据效力，不提供法律意见；
- 不生成“胜诉率”“法律责任”之类结论；
- 不破解、不恢复、不下载他人录音；
- 不上传任何文件到云端。

### 窗口机制

**完整语境不是固定 30–75 秒。** 从命中句出发，按相邻句的相关度继续扩展：

- 相邻句仍在同一段对话/同一件事 → 继续扩展；
- 遇到长静音（默认 20 秒）、话题切换、相关度明显下降 → 停止；
- 上限按录音长度自动计算（默认 30%，最多 10 分钟），避免把整通电话都算成“语境”。

完整语境内部再挑**最密的 25–75 秒**作为核心证据。这样既能保留“前面承认、后面改口”
的完整过程，又能快速定位最有力的那一句。

## Prerequisites

1. **Python 3.10+**，只用标准库，不需要 `pip install`。
2. **一份带时间戳的转写**（`.json` / `.srt` / `.vtt` / `.txt` / `.md`），
   最好带说话人标签。
3. **可选**：`ffmpeg`（截取证据片段和关键帧）。
4. **可选**：`faster-whisper`（从音视频本地生成转写）。

> 没有转写时，先运行 `scripts/transcribe.py`。本 Skill 不会自动上传音频。

## Workflow

```text
1. 确认录音来源合法、用户有权处理
2. 准备带时间戳的转写（没有就用 transcribe.py）
3. 把要证明的事写成一条具体主张
4. 运行 find_evidence.py
5. 阅读 evidence_report.md，人工核对原话
6. 用 verify_case.py 校验证据包
7. 由用户决定是否使用、如何提交
```

### 主张怎么写

一条主张只对应一个事实，最好包含**主体 + 行为 + 时间/条件**：

- ✅ “他什么时候承认这批货是延迟交付的”
- ✅ “谁承诺退款，承诺的时间是什么”
- ✅ “他有没有改口说没承诺过”
- ❌ “帮我找证据”（太宽泛）
- ❌ “证明他违约”（这是法律判断，不是定位问题）

## Core Commands

### 1. 定位证据并生成证据包

```bash
python scripts/find_evidence.py \
  --transcript examples/dispute-transcript.json \
  --claim "他什么时候承认这批货是延迟交付的" \
  --case-id DEMO-2026-1116 \
  --out out/admission
```

有原始录音/录屏时，加 `--media`，会额外生成核心证据、完整语境片段和关键帧：

```bash
python scripts/find_evidence.py \
  --transcript path/to/call.srt \
  --claim "谁承诺退款" \
  --media path/to/call.mp4 \
  --case-id CASE-001 \
  --out out/refund
```

只查看结果、不写文件：

```bash
python scripts/find_evidence.py \
  --transcript path/to/call.srt \
  --claim "他有没有改口" \
  --no-pack
```

### 2. 校验证据包

```bash
python scripts/verify_case.py out/admission
python scripts/verify_case.py out/admission.zip
```

输出 `PASS：所有文件哈希一致。` 表示证据包未被改动。

### 3. 可选：本地生成转写

```bash
python scripts/transcribe.py path/to/call.mp4 --out call.json --model small
```

需要 `ffmpeg` 和 `faster-whisper`；不联网。

### 4. 自测

```bash
python scripts/selftest.py
```

预期输出 `SELFTEST PASS`。

### 5. 打包提交

```bash
python scripts/package.py . --out ../submission/recording-evidence-finder.zip
```

检查文件数 ≤30、总大小 ≤40MB、`SKILL.md` ≤500 行、扩展名合规。

## Parameter Confirmation

| 参数 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `--transcript` | 是 | 带时间戳的转写 | `call.srt` |
| `--claim` | 是 | 一条具体事实主张 | `他什么时候承认延迟交付` |
| `--media` | 否 | 原始录音/录屏；有才生成 clip | `call.mp4` |
| `--case-id` | 否 | 材料/案件编号，写入 manifest | `CASE-001` |
| `--out` | 否 | 输出目录，默认 `./out` | `out/admission` |
| `--top` | 否 | 返回候选数，默认 3 | `5` |
| `--min-span` / `--max-span` | 否 | 完整语境；`max-span=0` 按录音长度自动 | `90` / `0` |
| `--core-min` / `--core-max` | 否 | 核心证据秒数，默认 25–75 | `25` / `75` |
| `--rel-threshold` / `--bridge` | 否 | 继续扩展的相关度阈值 / 允许跨过的低相关句数 | `0.15` / `4` |

运行前必须和用户确认：**录音来源是否合法、是否本人参与或已获许可**。
如果用户说不清来源，停止，不要生成证据包。

## Output

```text
out/admission/
├── result.json              # 机器可读：主张、片段、分数、命中理由
├── evidence_report.md       # 人读：时间戳、原话、上下文、复核步骤、边界说明
├── evidence_excerpt.txt     # 命中片段对应转写
├── manifest.json            # case_id、输入/输出文件清单 + SHA256
├── SHA256SUMS.txt           # 逐文件哈希
├── clip_core.mp4            # 可选：核心证据
├── clip_full.mp4            # 可选：完整语境
├── frames/frame_01..03.jpg  # 可选：关键帧
└── pack.zip                 # 以上文件的压缩包
```

`manifest.json` 记录：

- `case_id`、`claim`；
- `inputs`：原始媒体、转写文件的 SHA256 和大小；
- `segments`：时间窗、原话、命中理由；
- `outputs`：每个输出文件的 SHA256；
- `privacy`：`local-only; no network calls`；
- `disclaimer`：不构成法律意见。

## Compliance and Limits

> 必须遵守，写进每次输出。

1. **合法来源**：只处理用户本人参与的录音，或已获得明确许可的录音。
2. **不提供法律意见**：输出是“材料整理/辅助定位”，不是法律结论。
3. **不宣称司法采信**：哈希只能证明文件未被改动，不能证明内容真实或法院会采纳。
4. **最小必要**：只保留与主张相关的最短片段，避免扩散无关的第三方隐私。
5. **本地优先**：不上传、不调用云端 ASR、不把录音发给任何第三方。
6. **人工复核**：每条结果都要由用户对照原话确认，不能直接当作事实陈述。
7. **不伪造**：不剪辑、不变声、不拼接出原录音里没有的语义。

## Reference Documents

- [output-format.md](references/output-format.md)：`result.json`、`manifest.json` 字段。
- [asr-setup.md](references/asr-setup.md)：没有转写时怎么本地生成。
- [compliance.md](references/compliance.md)：合法来源、措辞边界、演示素材要求。
- [acceptance-criteria.md](references/acceptance-criteria.md)：比赛提交规范与自检清单。

## Examples

### 示例 1：定位“承认延迟交付”

```bash
python scripts/find_evidence.py \
  --transcript examples/dispute-transcript.json \
  --claim "他什么时候承认这批货是延迟交付的" \
  --case-id DEMO-2026-1116
```

预期：完整语境 `03:34 – 06:16`，核心证据 `04:12 – 04:46`，原话包含“我承认这批货是我们延迟交付的”。

### 示例 2：定位“承诺退款”

```bash
python scripts/find_evidence.py \
  --transcript examples/dispute-transcript.json \
  --claim "谁承诺退款" \
  --top 1
```

预期：完整语境 `06:16 – 08:00`，核心证据 `07:30 – 08:00`，原话包含“退款我这边可以承诺，月底之前原路退回”。

### 示例 3：输出给用户看

```text
找到 1 段证据候选：
完整语境 03:34 – 06:16（2 分 42 秒）
核心证据 04:12 – 04:46（34 秒）
“行，我承认这批货是我们延迟交付的，下周三之前一定补上。”

证据包：out/admission.zip
校验：python scripts/verify_case.py out/admission.zip
边界：本工具只做辅助定位与完整性校验，不构成法律意见。
```