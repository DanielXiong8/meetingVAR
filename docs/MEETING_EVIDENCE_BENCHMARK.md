# 会议证据基准：第一次真实数据基线

日期：2026-10-21

## 为什么做

meetingVAR 的核心承诺是“少存是成本，不丢是底线”。此前 495 项测试主要覆盖工程正确性，不能回答“这场会议最重要的事件有没有被保存”。本基准把事件级召回和误保存做成可回归指标。

## 工具

```bash
python scripts/meeting_benchmark.py --root <benchmark-root> --json report.json --md report.md
```

- case 目录 schema 见 `benchmarks/meeting_evidence/README.md`
- 支持 `labels.json` / `labels.csv`
- 支持 `predictions.json` 的 `runs[].clips`，也兼容 `current_mechanism_record.json` 的 `clip_window_s`
- 指标：事件召回、clip precision、每小时误保存分钟数、每小时保存分钟数、关注点覆盖、答案引用支持率（人工 `supported`）
- 发布门禁：默认 ≥20 场 `human_verified` 会议、≥40 事件、recall ≥80%、误保存 ≤10 分钟/小时

## test2 真实会议（机器预标注，未核验）

数据：`test2` 46 分钟线上会议；6 条与关注清单直接相关的预标注事件；当前机制 B0（热词触发 + 300 秒回溯）。

| 指标 | 结果 |
|---|---:|
| 事件召回 | 4 / 6 = 66.7% |
| 漏掉事件 | `evt_005`（KG 定位大讨论）、`evt_006`（agent-KG 衔接负责人） |
| clip precision | 100%（1 个片段，与 4 个事件重叠） |
| 保存时长 | 6.48 分钟/小时 |
| 误保存时长 | 3.05 分钟/小时 |
| 片段窗口 | 718.85–1018.85 秒 |

这个结果与历史复盘一致：回溯窗碰巧覆盖了 12–17 分钟的核心讨论，但完整错过 17:30–22:40 的 KG 定位大讨论。它证明当前热词机制**不能靠单次窗口命中当作事件召回**。

**标签状态是机器预标注，不能作为发布门禁真值。** 需要人工双标注后才允许进入 gate。

## 下一步（按顺序）

1. 对 test2 做整场双人标注，修正 6 条 seed 的时间边界/类型，并补齐空白区事件；负例单独标记。
2. 再收集 19 场真实会议（周会/组会/课堂/客户会），保持每场有明确关注点和事件时间。
3. 用同一批标签比较 B0 热词、L3 事件裁判、关注清单 + 滑窗三种机制。
4. 把 benchmark 接到发布门禁；未达到 20 场 human_verified 前，不允许宣称“核心价值已验证”。

## 指标解释

- **event recall**：标注为“需要保存”的事件中，被机制片段覆盖至少 50% 的比例；这是主指标。
- **false saved minutes/hour**：片段时间中不属于任何标注事件的部分，按会议小时归一；不是“片段数”。
- **clip precision**：片段与事件有有效重叠的比例；长片段会天然覆盖更多事件，因此必须和误保存时长一起看。
- **citation support rate**：人工判断引用是否支持结论，而不是引用 ID 是否存在。

## 仓库边界

真实会议音频、转写、标签和片段不进入 Git。`benchmarks/meeting_evidence/` 只保留 schema、说明和一个合成示例；真实 case 放在本机 `C:\Users\Lenovo\.zcode\workspace\default\meetingvar_benchmark\` 或其他私有目录。