# 输出格式

## result.json

```json
{
  "tool": "recording-evidence-finder",
  "tool_version": "0.1.0",
  "generated_at": "2026-11-17T10:00:00+0800",
  "case_id": "DEMO-2026-1116",
  "query": "他什么时候承认这批货是延迟交付的",
  "claim": "他什么时候承认这批货是延迟交付的",
  "source": "examples/dispute-transcript.json",
  "segments": [
    {
      "start": 214.0,
      "end": 376.0,
      "duration": 162.0,
      "start_text": "03:34",
      "end_text": "06:16",
      "core_start": 252.0,
      "core_end": 286.0,
      "core_duration": 34.0,
      "core_start_text": "04:12",
      "core_end_text": "04:46",
      "turn_range": [12, 21],
      "core_turn_range": [14, 15],
      "speaker": "对方",
      "text": "[03:34] 我方: ...\n[04:12] 对方: ...",
      "core_text": "[04:12] 对方: ...\n[04:28] 我方: ...",
      "quote": "行，我承认这批货是我们延迟交付的，下周三之前一定补上。",
      "score": 1.0,
      "raw_score": 64.2,
      "why": ["承认", "延迟交付", "下周三"]
    }
  ],
  "disclaimer": "本工具只做录音材料的辅助定位与完整性校验，不构成法律意见……",
  "notes": []
}
```

| 字段 | 含义 |
|---|---|
| `case_id` | 材料/案件编号，用户提供 |
| `claim` | 本次要定位的事实主张 |
| `start` / `end` | **完整语境**，按相关度自适应扩展，不是固定 30–75 秒 |
| `core_start` / `core_end` | **核心证据**：区间内最密的 25–75 秒 |
| `quote` | 核心证据里最匹配的一句原话，必须人工核对 |
| `text` / `core_text` | 完整语境 / 核心证据的上下文（含说话人） |
| `why` | 命中的短语，不是法律判断 |
| `disclaimer` | 边界说明，随结果一起输出 |

> 为什么要两个区间：只给 30 秒，可能看不到“他前面承认、后面又改口”的完整过程；
> 只给 3 分钟，又不方便快速核对。完整语境用于判断，核心证据用于快速定位和展示。

## manifest.json

```json
{
  "tool": "recording-evidence-finder",
  "tool_version": "0.1.0",
  "generated_at": "2026-11-17T10:00:00+0800",
  "case_id": "DEMO-2026-1116",
  "query": "...",
  "claim": "...",
  "inputs": [
    {"path": "call.mp4", "sha256": "…", "bytes": 12345678},
    {"path": "call.srt", "sha256": "…", "bytes": 2345}
  ],
  "segments": [...],
  "outputs": [
    {"path": "result.json", "sha256": "…", "bytes": 2345}
  ],
  "privacy": "local-only; no network calls",
  "disclaimer": "...",
  "notes": []
}
```

`inputs` 里同时记录原始媒体和转写，才能证明“定位结果是从这份材料里来的”。
如果只提供转写、没有原始媒体，`inputs` 就只有转写；报告中会注明。

## 校验规则

`verify_case.py`：

1. 逐行校验 `SHA256SUMS.txt`；
2. 校验 `manifest.json` 里 `outputs` 的哈希；
3. 任何不一致 `exit 1`，并打印文件名。

哈希能证明“包内文件未被改动”，不能证明：
- 录音本身未被剪辑（除非用户提供原始载体）；
- 内容真实；
- 司法机关会采信。

这些边界必须写进报告，不能省略。