# 项目记忆真人评测（30 天纵向）

## 为什么还需要这一层

合成基准能防回归，但不能证明“真实用户在 30 天里越用越准”。真实评测必须有人写问题、有人判断“这条记忆是否回答了问题”，以及跨天观察事实更新/过期/冲突。仓库里的 `scripts/memory_human_eval.py` 就是这条流水线，**不会用模型冒充真人标签**。

## 评测设计

建议每个用户连续使用 30 天，项目记忆自然增长；第 30 天做一次盲测：

- 从 30 天内产生的事实中分层抽样 30 条：决定、承诺、行动项、风险、未决问题各 6 条；
- 出题人只看事实描述，不看检索结果，写一个自然问题；
- 另一名标注者独立判断：
  - `label_relevant`：问题是否确实在问这条事实；
  - `label_correct_source`：系统 top1 是否引用了正确事实；
  - `label_stale`：系统是否把已过期/被替代事实当成当前事实；
- 争议由第三人裁决；保留原始 CSV 和裁决记录。

建议 5–10 名用户、每人 30 题，共 150–300 题；报告均值、95% 置信区间和样本量。不要只报一个没有样本量的“准确率”。

## 导出标注模板

```bash
python scripts/memory_human_eval.py \
  --project "D:/项目/某项目" \
  --export memory_labels.csv \
  --samples 30
```

CSV 列：

| 列 | 含义 |
|---|---|
| `id` | 题号 |
| `question` | 出题人填写的自然问题（导出时为空） |
| `expected_fact` | 抽样事实名称（出题人不应据此反推问题） |
| `fact_type` / `created_at` | 分层与时间信息 |
| `label_relevant` | 0/1 |
| `label_correct_source` | 0/1 |
| `label_stale` | 0/1 |
| `notes` | 备注 |

## 计算指标

```bash
python scripts/memory_human_eval.py \
  --project "D:/项目/某项目" \
  --labels memory_labels.csv \
  --out memory_eval.json
```

输出：

- `recall_at_5`：期望事实是否在 top5；
- `top1_accuracy`：top1 是否就是期望事实；
- `human_relevant_rate`：标注者认为问题确实在问该事实；
- `human_correct_source_rate`：top1 引用正确；
- `human_stale_rate`：把过期/被替代事实当当前事实的比例。

空白标签不会被当成 0；只有 `question` 非空的行进入统计。

## 30 天纵向模拟（不是真人）

`benchmarks/memory/longitudinal_30d.json` + `scripts/memory_longitudinal_eval.py` 提供一个可重复的 30 天时间线模拟：12 个事件、事实更新、冲突、冲突解决、过期和 30 天后的 10 个查询。

```bash
python scripts/memory_longitudinal_eval.py \
  --json memory_30d.json --md memory_30d.md
```

当前模拟结果只用于回归：recall@5 1.000、负对照 1.000、冲突识别 True、过期默认视图干净 True、重载持久化 True。它**不是**真实用户 30 天数据，也不能替代上面的真人标注。

## 隐私

- 评测数据只在本机项目目录和标注 CSV 中；
- 导出前删除姓名、客户名、密钥等敏感字段，或使用脱敏项目副本；
- 不把真实会议转写/记忆上传到第三方评测平台，除非获得明确授权。