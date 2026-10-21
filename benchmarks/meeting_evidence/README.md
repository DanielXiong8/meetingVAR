# 会议证据基准（Meeting Evidence Benchmark）

目的：把“选择性取证”从设计原则变成可回归的指标。真实会议数据不进入仓库。

## 一个 case 的目录

```text
<case_id>/
  manifest.json
  labels.json        # 或 labels.csv
  predictions.json   # 可选：当前机制实际保存的片段
  answers.json       # 可选：Agent 问答 + 人工 supported 标记
```

`manifest.json`：

```json
{
  "case_id": "team_weekly_001",
  "title": "真实周会 2026-10-20",
  "duration_s": 2776,
  "labels": "labels.json",
  "labels_status": "human_verified",
  "predictions": "predictions.json",
  "answers": "answers.json"
}
```

`labels_status` 只能是 `human_verified` 或 `unverified`；机器预标注不得当成真值通过发布门禁。

## labels.json

```json
{"events": [
  {"event_id":"evt_001","start_time":750,"end_time":812,
   "event_type":"COMMITMENT","event_status":"assigned",
   "focus_ids":["focus_2"],"requires_capture":true,
   "evidence_turn_ids":["turn_130"],"needs_human_review":false}
]}
```

CSV 支持列：`event_id,start_time,end_time,event_type,event_status,focus_ids,requires_capture,evidence_turn_ids,needs_human_review`。

## predictions.json

```json
{"runs": [
  {"name":"B0_hotword","clips":[
    {"clip_id":"c1","start_time":718.85,"end_time":1018.85}
  ]}
]}
```

也兼容 `current_mechanism_record.json` 的 `clip_window_s`。

## 指标

- `event_recall`：需要保存的事件中，被至少一个片段覆盖 ≥50% 的比例
- `clip_precision`：片段中与任一标注事件有有效重叠的比例
- `false_saved_minutes_per_hour`：片段中不属于任何标注事件的时间，按会议小时归一
- `saved_minutes_per_hour`：每小时实际持久化时长
- `focus_coverage`：每个关注点是否至少有一个事件被覆盖
- `citation_support_rate`：Agent 答案中人工标记 `supported=true` 的比例（不是“引用存在率”）

## 运行

```bash
python scripts/meeting_benchmark.py --root benchmarks/meeting_evidence \
  --json report.json --md report.md
```

发布门禁默认要求：≥20 场 `human_verified` 会议、≥40 个事件、micro recall ≥80%、误保存 ≤10 分钟/小时。未达到会以非零退出码失败。

## 标注纪律

1. 先整场闭卷标注“值得保存的事件”，再看机制输出；
2. 双人独立标注，分歧由第三人裁决；
3. 负例（明确不值得保存的片段）单独记录；
4. 标签和时间边界一经进入门禁，不得在看到模型结果后修改；
5. 指标退步不能靠调阈值掩盖，必须回到数据解释。