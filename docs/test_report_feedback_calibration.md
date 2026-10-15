# 项目反馈驱动的 L2 阈值校准测试报告

日期：2026-10-07  
基线：`codex/b-feedback-loop` (`edbd1cf`)  
实现：项目级 `meetingvar/calibration.json`，不改 `Clip` schema。

## 行为

- 仅接受带可解析 `interest_score` 且状态为 `useful` / `useless` 的 L2 片段。
- useful 小幅降低阈值；useless 将阈值提高到该误命中分数以上；范围限制为 0.05–0.95。
- 通过 clip ID 幂等更新；修正旧评价会更新同一条样本，不会重复计数。
- `go`、`record --memory`、`replay --memory` 读取项目阈值；没有项目校准文件时沿用全局默认值。
- `review --project <路径>` 显式将当前会话评价纳入项目校准；不自动跨项目混样。

## 验证

| 检查 | 结果 |
|---|---|
| 有用/无用 L2 样本改动阈值 | 通过 |
| L1 样本不会改动 L2 阈值 | 通过 |
| 重复扫描幂等，评价修订覆盖旧样本 | 通过 |
| 校准值进入下一会话 InterestScorer | 通过 |
| `python scripts/demo_feedback_loop.py` | 通过（两场，无模型/设备/网络，临时数据自动清理） |
| 完整 model-free 单元测试 | 10/10 通过（Python 3.14.4） |

## 边界

阈值变化是小步长、可解释的启发式，不是训练模型，也不能据合成数据声称准确率提升。尚未用真实会议评价样本验证长期收益；桌面 UI、真实录音素材和 Python 3.10/模型环境端到端回归也未在本次测试中验证。
