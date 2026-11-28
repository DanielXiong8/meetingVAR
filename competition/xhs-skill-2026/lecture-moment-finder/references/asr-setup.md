# 没有转写怎么办

核心 Skill 只吃转写，不强制装 ASR。按成本从低到高选：

## 方案 A：用录课软件自带字幕（最快）

- 腾讯会议 / 飞书 / Zoom / OBS 等录屏工具通常能导出 `.srt` 或 `.vtt`；
- 导出后直接：

```bash
python scripts/find_moment.py --transcript lecture.srt --query "老师讲考试范围"
```

## 方案 B：本地 faster-whisper（推荐，不联网）

```bash
pip install faster-whisper
python scripts/transcribe.py lecture.mp4 --out lecture.json --model small
```

要求：

- `ffmpeg` 在 PATH 中；
- 首次运行会下载模型（`tiny`/`base`/`small`/`medium`），之后离线可用；
- CPU 可跑，`small` 在普通笔记本上大约是 3–8 倍速；
- 不调用任何云端接口。

模型大小参考：

| 模型 | 大致大小 | 中文效果 | 适用 |
|---|---|---|---|
| tiny | ~75MB | 一般 | 快速试跑 |
| base | ~145MB | 可用 | 清晰录音 |
| small | ~480MB | 较好 | 课堂录音默认 |
| medium | ~1.5GB | 更好 | 有噪音/口音 |

## 方案 C：已有文字稿

如果老师给了讲义或文字稿，但只有段落没有时间戳：

- 用 `.txt` 每行写成 `[MM:SS] 内容`；
- 或者先粗对齐（例如每 30 秒一行），再用 `--min-span` / `--max-span` 控制完整讨论的长度。

没有时间戳就没有“第几分钟”，只能做全文检索，不能回放定位。

## 常见报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `未找到 ffmpeg` | PATH 里没有 ffmpeg | 安装 ffmpeg，或改用方案 A |
| `未安装 faster-whisper` | 依赖缺失 | `pip install faster-whisper` |
| `no timestamped turns parsed` | 转写没有时间戳 | 换 SRT/VTT/JSON，或按 `[MM:SS]` 补时间 |
| 片段总是偏移几秒 | 转写和媒体起点不一致 | 用 `--media` 时确认两者从同一时刻开始 |