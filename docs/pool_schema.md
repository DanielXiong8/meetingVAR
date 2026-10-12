# 待审片段池 · 接口契约 v0

这是 A（流水线工程）与 B（产品/智能层）的**接口契约**：A 的"命中转正"往池里写，
B 的"会后厚薄纪要/评价闭环"从池里读。**字段变更须两人确认，并同步修改
`src/meetingvar/pool/schema.py`（代码化定义）与本文件。**

## 数据结构

```json
{
  "clip_id": "uuid4",
  "session_id": "20261012_groupmeeting_01",
  "created_at": "2026-10-12T14:32:05+08:00",
  "trigger": {
    "layer": "L1 | L2 | L3",
    "evidence": "热词:'测序污染' | score=0.87 | manual_hotkey",
    "explanation": "可解释字段，答辩演示用"
  },
  "audio":  { "path": "data/clips/<clip_id>/<clip_id>.wav", "start_ts": 812.4, "end_ts": 934.1 },
  "screen": { "video_path": null, "keyframes": ["<clip_id>/screens/frame_0000.jpg"], "hd_clip_dir": "data/hd/hd_..." },
  "transcript": [
    { "ts": 815.2, "speaker": "S2", "text": "……" }
  ],
  "review": { "status": "pending | useful | useless", "note": null }
}
```

## 设计意图（别改丢了）

1. **audio/screen 只在命中转正时落盘**：草稿层常驻内存态（环形缓冲），默认零落盘——这是隐私卖点的技术保证。
   **未命中（零片段）的会话零落盘**：连 `pool/<session_id>/` 目录都不创建，草稿转写随会话结束消失
   （审计 G1a/G1b，`tests/audit_zero_persistence.py`）。命中会话（G1c）才落盘：
   片段 + 全场转写文本（`transcript.json`，薄纪要原料）+ `summary.json`。
2. **trigger.evidence 强制可解释**：为什么命中（哪个词/多少分/谁按的键）必须留痕，§6 答辩"与 Fireflies 区别"和 L2 消融实验都靠它。
3. **transcript 带说话人标签**：厚纪要"谁在什么时候说了什么"（PI 插话归因）是刚需，D7 定案。
4. **review 是评价闭环的数据入口**：F4 的"有用/没用+补标"直接写这里；显式评价过且含 `interest_score` 的 L2 片段可进入项目级阈值校准。L1/L3 不作为 L2 样本。
5. **ts 一律用会议内相对秒**：绝对时间只在 session 级记录一次，避免跨设备时钟问题。
6. **audio.start_ts/end_ts 是实际保存 WAV 的物理窗口，使用会议相对秒**：
   窗口 = `[max(可用音频起点, trigger-LOOKBACK), trigger]`，两端按采样点精确裁剪；
   有音频但无转写的手动 VAR 仍记录实际音频时间，不回退到 `0.0/0.0`。
   采集时间轴断流时保留原有时间位置，缺失区间补静音，不把后续发言提前；转写句的时间仍独立保留在 `transcript[].ts`。
   本条说明已有字段的当前含义，不新增或改名 Clip 字段。事件评估读取媒体窗口，长回溯可能合并相邻事件，不能据单测推断真实触发 P/R。

## 存放约定

- 片段元数据：`data/pool/<session_id>/<clip_id>.json`
- 音频：`data/clips/<clip_id>/<clip_id>.wav`；关键帧：`data/clips/<clip_id>/screens/frame_*.jpg`
- 高清视频：Clip 的 `screen.hd_clip_dir` 指向 `data/hd/hd_*/`，其中保存 MP4 或失败时的 raw 帧和 meta（均不入库）
