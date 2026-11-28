# 实时模式（Live Mode）

实时模式是“meetingVAR 的 Skill 版”：上课前写下关心的问题，启动后自动录音/录屏，
实时转写并判断当前内容是否命中；命中后不会立刻截断，而是继续跟踪到老师讲完这个
知识点，再保存完整区间的音频、屏幕录像和原话（例如 01:30–06:30 全程）。

## 1. 依赖

实时模式需要本机 Python 3.10+ 以及：

| 包 | 用途 |
|---|---|
| `pyaudiowpatch` | Windows WASAPI 系统声音环回 + 麦克风 |
| `mss` + `Pillow` | 屏幕采集 |
| `av`（PyAV） | 屏幕录像编码（不需要外部 ffmpeg） |
| `numpy` | 音频转 float32 给 faster-whisper |
| `faster-whisper` | 本地实时转写 |

如果这些已经装在 meetingVAR 的引擎环境里，直接用：

```bash
"D:\miniconda3\envs\meetingvar\python.exe" scripts/live_ctl.py doctor
```

不需要在 CodeArts 的 Python 里重复安装。`live_ctl.py start` 支持 `--python` 指定解释器。

## 2. 配置文件

```json
{
  "session_id": "lecture-2026-11-17",
  "title": "数据结构 第 7 讲",
  "consent": false,
  "save_transcript": true,
  "interests": [
    {
      "id": "dp",
      "question": "老师讲动态规划状态转移方程的那几分钟",
      "keywords": ["动态规划", "状态转移方程", "状态定义"],
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
    "monitor": 1,
    "audio_device": null,
    "mic_device": null
  },
  "asr": {
    "model": "base",
    "language": "zh",
    "device": "cpu",
    "chunk_seconds": 10.0,
    "step_seconds": 3.0,
    "vad_filter": true,
    "vad_rms": 180,
    "initial_prompt": "以下是普通话课堂内容，请用简体中文转写。"
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

### 字段说明

| 字段 | 说明 |
|---|---|
| `session_id` | 会话 ID，输出目录使用 |
| `consent` | 是否已确认录音/录屏许可；也可用 `--consent` |
| `save_transcript` | 是否保留实时转写；隐私优先时设 false |
| `interests[].question` | 一句具体问题，参与打分 |
| `interests[].keywords` | 硬条件，至少命中 1 个才可能触发 |
| `min_score` | 分数阈值；误报多就调高 |
| `min_keyword_hits` | 至少命中几个关键词 |
| `consecutive_hits` | 连续几个窗口超过阈值才触发；2 更稳但更慢 |
| `cooldown_seconds` | 同一问题两次命中的最短间隔 |
| `capture.source` | `loopback` / `mic` / `both` |
| `capture.screen` | 是否录屏 |
| `capture.fps` | 截图/前段帧采样率，1–2 足够 |
| `capture.video_fps` | 录屏帧率，默认 5 |
| `capture.video_width` | 录屏宽度，默认 1280 |
| `capture.video_crf` | 录屏质量（CRF 越大文件越小），默认 28 |
| `capture.monitor` | 录制哪个显示器，默认 1（主屏） |
| `asr.model` | `tiny` / `base` / `small` / `none`（none 只测采集） |
| `asr.chunk_seconds` | 每次转写的音频窗口，默认 10 秒 |
| `asr.step_seconds` | 多久跑一次，默认 3 秒 |
| `asr.vad_filter` | 是否先做 VAD；失败会自动回退 |
| `asr.initial_prompt` | 用简体中文提示，避免繁体输出 |
| `trigger.window_seconds` | 匹配时回看多少秒转写 |
| `trigger.ring_seconds` | 音频/屏幕环形缓冲秒数；决定最多能向前回溯多久 |
| `trigger.min_span` | 命中区间最短秒数 |
| `trigger.max_span` | 单个区间最长秒数；超长知识点自动分段 |
| `trigger.end_grace_seconds` | 主题结束后再等多久收尾（给 ASR 延迟留余量） |
| `trigger.rel_threshold` | 继续跟踪的相关度阈值（相对峰值） |
| `trigger.bridge` | 允许跨过几个低相关句继续跟踪 |
| `trigger.max_gap` | 相邻两句间隔超过多少秒视为停顿 |

## 3. 命令

```bash
# 依赖 + 设备
python scripts/live_ctl.py doctor
python scripts/live_ctl.py devices

# 启动（后台）
python scripts/live_ctl.py start \
  --config examples/interests.example.json \
  --out out/lecture-2026-11-17 \
  --consent

# 使用指定 Python（依赖装在 meetingVAR 环境时）
python scripts/live_ctl.py start \
  --config examples/interests.example.json \
  --out out/lecture-2026-11-17 \
  --consent \
  --python "D:\miniconda3\envs\meetingvar\python.exe"

# 前台调试
python scripts/live_ctl.py start ... --foreground --duration 30

# 状态 / 监听命中 / 停止
python scripts/live_ctl.py status --out out/lecture-2026-11-17
python scripts/live_ctl.py watch --out out/lecture-2026-11-17
python scripts/live_ctl.py stop --out out/lecture-2026-11-17
```

## 4. 输出

```text
out/lecture-2026-11-17/
├── status.json
├── transcript.jsonl
├── hits.jsonl          # hit_open / hit_close 事件流
├── session.json
├── engine.log
└── hits/
    └── hit_001/
        ├── audio.wav    # 完整知识点区间
        ├── screen.mp4   # 同一区间的屏幕录像（5fps / 1280 宽）
        ├── transcript.txt
        └── hit.json     # 区间、核心片段、原话、结束原因
```

没有 PyAV 时，`screen.mp4` 会自动回退为 `screens/` 关键帧截图（最多 60 张）。

`status.json` 是给 CodeArts agent / HUD 轮询的：

```json
{
  "state": "recording",
  "elapsed": 1320.5,
  "hits": 2,
  "last_text": "状态转移方程是动态规划的核心。",
  "open_hits": [
    {
      "hit_id": "hit_003",
      "interest_id": "dp",
      "at": 1300.0,
      "span_start_text": "21:40",
      "duration": 20.5
    }
  ],
  "last_hit": {
    "interest_id": "dp",
    "span_start_text": "18:20",
    "span_end_text": "20:05",
    "duration": 105.0,
    "core_start_text": "18:55",
    "core_end_text": "19:50",
    "key_quote": "状态定义是骨架，转移方程是肌肉。",
    "dir": "out/lecture/hits/hit_002"
  }
}
```

`hits.jsonl` 有两类事件：`hit_open`（开始抓取）与 `hit_close`（收尾完成，
带完整区间、核心片段和 `dir`）。

## 5. 与 meetingVAR 的功能对齐

| meetingVAR | Skill 实时模式 | 状态 |
|---|---|---|
| 系统声音环回 + 麦克风 | `capture.source=loopback/mic/both` | ✅ |
| 屏幕采集 | `capture.screen` + `ScreenRing` | ✅ |
| 实时 ASR | faster-whisper 分块 + 重叠拼接 | ✅ |
| 课前关注清单 | `interests.json` | ✅ |
| 关键词/兴趣触发 | 关键词 + 短语加权 + 阈值 | ✅ |
| 完整知识点区间 | 命中后跟踪 + 自适应区间（不是固定 90 秒） | ✅ |
| 屏幕录屏 | `screen.mp4`，覆盖完整区间（PyAV，无需 ffmpeg） | ✅ |
| 可回放材料 | `audio.wav` / `screen.mp4` / `transcript.txt` / `hit.json` | ✅ |
| 证据包 + SHA256 | 不在学习 Skill；见法律取证 Skill | — |
| 课后 Find Moment | `find_moment.py` | ✅ |
| 同意/本地优先 | `--consent` / local-only | ✅ |
| 语义/LLM 兴趣判断 | 未实现 | ⬜ P1 |
| 说话人分离 | 未实现 | ⬜ P1 |
| 高清补录（HD） | 未实现 | ⬜ P1 |
| HUD 副屏 | 未实现（用 status.json 轮询） | ⬜ P1 |
| 承诺账本 | 不适用于课堂 | — |

## 6. 调参

| 现象 | 调整 |
|---|---|
| 命中太频繁 | 提高 `min_score`、`cooldown_seconds`、`consecutive_hits` |
| 总是漏 | 降低 `min_score`、补充 `keywords`、降低 `min_keyword_hits` |
| 转写太慢 | `model: "tiny"`，或增大 `step_seconds` |
| 转写太碎 | 增大 `chunk_seconds`，或 `model: "small"` |
| 繁体输出 | 保留 `initial_prompt` 的“请用简体中文转写” |
| 区间太短/提前收尾 | 提高 `end_grace_seconds`、降低 `rel_threshold`、提高 `max_gap` |
| 区间太长 | 降低 `end_grace_seconds`、降低 `max_span` |
| 一个知识点被拆成多段 | 提高 `max_span`、提高 `end_grace_seconds` |
| 只想测试采集 | `asr.model: "none"` |

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `pyaudiowpatch` 缺失 | CodeArts 的 Python 没装依赖 | `--python` 指向 meetingVAR 引擎环境 |
| 没有环回设备 | 系统输出不是 WASAPI / 被独占 | `devices` 查看；改用 `mic` 或 `both` |
| `doctor` 正常但没转写 | 音频太轻 / 窗口没覆盖语音 | 检查 `status.last_text`；降低 `vad_rms` |
| 命中一直不触发 | 关键词/阈值太严 | 降低 `min_score`；确认关键词确实出现 |
| 提示 `initial_prompt` | 空白音频导致 Whisper 复读提示词 | 代码已过滤；仍出现就降低 `vad_rms` |
| 后台进程被父进程带走 | CodeArts 使用 job object | 改用 `--foreground --duration`，或从独立终端启动 |
| 录屏/截图失败 | 无桌面会话/权限 | 设 `capture.screen=false`，只用音频 |
| 没有 `screen.mp4`，只有 `screens/` | PyAV 不可用或录屏启动失败 | `python -c "import av"` 检查；`pip install av` |

## 8. 已知限制

- 分块 ASR 在句首/句尾会有少量误差；命中片段以 `audio.wav` + 原话为准。
- 说话人分离未实现；在线课通常只有老师一路，问题不大。
- 没有语义判断；“老师讲的东西很重要”这类问题无法触发，需写成具体知识点。
- 屏幕录像默认 5fps、1280 宽，用于回放定位，不是高清录屏；没有 PyAV 时回退为截图。
- 实时模式依赖本机设备；评委/网页环境请用 `simulate` 回放演示。
- 超长知识点（超过 `max_span`）会自动分成前后两段，分段处不重复、不丢内容。