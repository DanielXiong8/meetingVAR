# 输出格式

## result.json

```json
{
  "tool": "lecture-moment-finder",
  "tool_version": "0.1.0",
  "generated_at": "2026-11-17T10:00:00+0800",
  "query": "老师讲动态规划状态转移方程的那几分钟",
  "source": "examples/lecture-transcript.json",
  "segments": [
    {
      "start": 2400.0,
      "end": 2688.0,
      "duration": 288.0,
      "start_text": "40:00",
      "end_text": "44:48",
      "core_start": 2480.0,
      "core_end": 2548.0,
      "core_duration": 68.0,
      "core_start_text": "41:20",
      "core_end_text": "42:28",
      "turn_range": [62, 70],
      "core_turn_range": [64, 65],
      "speaker": "王老师",
      "text": "[40:00] 王老师: ...\n[41:20] 王老师: ...",
      "core_text": "[41:20] 王老师: ...\n[42:00] 王老师: ...",
      "quote": "动态规划的核心不是背代码，而是先把状态定义清楚，再写状态转移方程。",
      "score": 1.0,
      "raw_score": 78.7,
      "why": ["状态转移方程", "动态规划", "状态转移", "转移方程"]
    }
  ],
  "notes": []
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `start` / `end` | **完整讨论区间**，按相关度自适应扩展，不是固定 30–90 秒 |
| `start_text` / `end_text` | 完整讨论的可读时间戳 |
| `core_start` / `core_end` | **核心片段**：区间内最密的 30–90 秒，用于快速回放 |
| `core_start_text` / `core_end_text` | 核心片段的可读时间戳 |
| `turn_range` | 完整讨论覆盖的转写句下标范围（含两端） |
| `core_turn_range` | 核心片段覆盖的转写句下标范围 |
| `quote` | 最匹配的一句原话，用来快速核对 |
| `text` | 完整讨论内的上下文 |
| `core_text` | 核心片段内的上下文 |
| `score` | 归一化到 0–1 的匹配分，仅供排序 |
| `why` | 命中的短语，不是“模型解释” |

> 为什么分成两个区间：用户问“哪几分钟”时，往往既要**完整语境**（老师从哪讲到哪），
> 又要**最短回放**（最关键的几十秒）。一个固定窗口只能二选一。

## 实时模式：hits/hit_NNN/hit.json

```json
{
  "event": "hit_close",
  "hit_id": "hit_001",
  "session_id": "lecture-2026-11-17",
  "interest_id": "dp",
  "interest": "老师讲动态规划状态转移方程的那几分钟",
  "at": 1300.0,
  "score": 63.4,
  "why": ["状态转移方程", "动态规划"],
  "quote": "今天我们来学习动态规划的状态转移方程。",
  "span_start": 90.0,
  "span_end": 390.0,
  "duration": 300.0,
  "span_start_text": "01:30",
  "span_end_text": "06:30",
  "core_start": 110.0,
  "core_end": 178.0,
  "core_duration": 68.0,
  "core_start_text": "01:50",
  "core_end_text": "02:58",
  "key_quote": "状态定义是骨架，转移方程是肌肉。",
  "key_quote_at": "02:10",
  "close_reason": "topic_end",
  "lines": 32,
  "audio": "audio.wav",
  "screen_video": "screen.mp4",
  "screens": 0,
  "dir": "out/lecture/hits/hit_001"
}
```

| 字段 | 含义 |
|---|---|
| `span_start` / `span_end` | **完整知识点区间**：命中后跟踪到主题结束，不是固定 90 秒 |
| `core_start` / `core_end` | 区间内最密的 30–90 秒，用于快速回放 |
| `quote` | 触发命中时的那句话 |
| `key_quote` / `key_quote_at` | 整个区间里最相关的一句原话及其时间 |
| `close_reason` | `topic_end` / `gap` / `break` / `max_span` / `session_stop` |
| `screen_video` | 屏幕录像文件名；PyAV 不可用时为 `null` |
| `screens` | 回退截图的张数；正常录屏时为 0 |
| `dir` | 该命中的目录；里面有 `audio.wav` / `screen.mp4` / `transcript.txt` |

`hits.jsonl` 里同一目录还会有一条 `hit_open` 事件，表示“开始抓取”。
`status.json` 的 `open_hits` 显示当前正在抓取的区间。

## 课后模式：输出文件

```text
out/dp-moment/
├── result.json             # 结构同上面的 result.json
├── report.md               # 可读报告（含“如何回放”）
├── transcript_excerpt.txt  # 命中区间的原话
├── clip_core.mp4           # 有 --media 且本机有 ffmpeg
├── clip_full.mp4
└── frames/                 # 核心片段关键帧
```

## 为什么没有 manifest / SHA256

学习 Skill 的定位是“找到 + 回放”，不承担证据完整性证明：

- 不生成 `manifest.json`；
- 不生成 `SHA256SUMS.txt`；
- 不打包 zip。

需要文件哈希、原始媒体哈希和证据包校验时，使用法律取证 Skill
（`recording-evidence-finder`）的 `verify_case.py`。