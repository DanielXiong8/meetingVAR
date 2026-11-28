# 没有转写怎么办

核心 Skill 只吃转写，不强制装 ASR。按成本从低到高选：

## 方案 A：用录音/录屏软件自带字幕（最快）

- 腾讯会议 / 飞书 / Zoom / OBS 等通常能导出 `.srt` 或 `.vtt`；
- 导出后直接：

```bash
python scripts/find_evidence.py \
  --transcript call.srt \
  --claim "他什么时候承认延迟交付"
```

## 方案 B：本地 faster-whisper（推荐，不联网）

```bash
pip install faster-whisper
python scripts/transcribe.py call.mp4 --out call.json --model small
```

要求：

- `ffmpeg` 在 PATH 中；
- 首次运行会下载模型（`tiny`/`base`/`small`/`medium`），之后离线可用；
- CPU 可跑，`small` 在普通笔记本上大约是 3–8 倍速；
- 不调用任何云端接口，录音不出本机。

模型大小参考：

| 模型 | 大致大小 | 中文效果 | 适用 |
|---|---|---|---|
| tiny | ~75MB | 一般 | 快速试跑 |
| base | ~145MB | 可用 | 清晰通话 |
| small | ~480MB | 较好 | 默认 |
| medium | ~1.5GB | 更好 | 有噪音/口音 |

## 方案 C：已有文字稿

如果已有文字记录，但只有段落没有时间戳：

- 用 `.txt` 每行写成 `[MM:SS] 内容`；
- 或者按录音的实际节点粗对齐；
- 没有时间戳就没有“第几分钟”，只能做全文检索，不能回放定位。

## 说话人标签

证据场景建议保留说话人：

- SRT/VTT 每行写成 `对方: 原话`；
- JSON 用 `"speaker": "对方"`；
- 没有说话人标签也能定位，但报告里只能显示“未知说话人”。

## 常见报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `未找到 ffmpeg` | PATH 里没有 ffmpeg | 安装 ffmpeg，或改用方案 A |
| `未安装 faster-whisper` | 依赖缺失 | `pip install faster-whisper` |
| `no timestamped turns parsed` | 转写没有时间戳 | 换 SRT/VTT/JSON，或按 `[MM:SS]` 补时间 |
| 片段总是偏移几秒 | 转写和录音起点不一致 | 用 `--media` 时确认两者从同一时刻开始 |
| 说话人串位 | 转写没有分离说话人 | 用支持 diarization 的工具，或手工标注 |