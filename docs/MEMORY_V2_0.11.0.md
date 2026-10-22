# 项目记忆 v2（0.11.0，2026-10-22）

这版把 meetingVAR 的 memory 从“能写、能按子串搜”推进到可商用治理的闭环：

- **写入**：聊天里显式说“记住…”即可生成待确认事实；会议结束自动弹出快速事实候选；
- **召回**：SQLite FTS5 + 中文 bigram + BM25，统一主聊天与记忆问答，返回分数和命中原因；
- **治理**：版本快照、生效/过期/替代/待确认冲突、置顶、软删除、编辑、冲突三选一；
- **跨会话**：对话滚动摘要 + `search_conversations`/`read_conversation` 工具与引用；
- **评测**：40 题合成记忆基准，覆盖直接查询、同义表达、类型过滤、冲突、过期和负对照。

Markdown 事实文件仍然是唯一事实源；`memory.sqlite3` 是可重建索引，`MEMORY.md` 是可重建的人读索引。

## 1. 数据模型

每条事实仍是 `<项目>/meetingvar/agent_memory/facts/<slug>.md`，frontmatter 中的 metadata 新增：

| 字段 | 含义 |
|---|---|
| `status` | `active` / `superseded` / `expired` / `pending-conflict` / `rejected` / `deleted` |
| `canonical_key` | 类型 + 规范化描述，用于同一事实跨标题合并 |
| `version` | 每次写入/编辑/状态变更递增 |
| `valid_from` / `valid_to` | 生效区间；`valid_to` 到点后默认检索不再返回 |
| `supersedes` / `superseded_by` | 新旧事实链路 |
| `conflict_with` | 待确认冲突的旧事实名称列表 |
| `source_refs` / `source_meetings` / `source_count` | 原句 turn 与来源会议，跨会议累计 |
| `pinned` / `tags` | 置顶与标签 |

覆盖版本前会把旧内容快照到 `agent_memory/versions/<slug>/vN.md`，因此覆盖不是不可逆的。

## 2. 召回：FTS5 + BM25

`agent/memory_index.py` 建立可丢弃的 SQLite FTS5 索引：

- 中文按“单字 + bigram + 短整词”写入 `tokens`，解决 SQLite unicode61 不切中文的问题；
- 查询用 bigram / 英文词，`MATCH` 后用 `bm25()` 排序；
- 排序分 = BM25 + 类型权重 + 新近度 + 置信度 + 来源数 + 置顶 + 命中词数；
- 返回 `score` 和 `why`（命中的词），让用户知道“为什么是这条”；
- 支持 `types`、`status`、`origin_meeting`、`pinned`、`include_expired` 过滤；
- 内置少量业务同义词（交付/上线/发布/发版、截止/期限、负责人/owner、决定/决议…）；
- 单字泛词（“流程”“安排”“时间”等）只在没有更强命中时不会单独把结果带出来；
- 查询含日期时要求事实也含该日期，避免“改到12月”命中“11月15日”的旧事实；
- FTS 不可用时退回确定性关键词扫描，功能不中断。

主聊天 `search_memory`、`read_fact`、`MemoryQAAgent` 已统一走同一个 `FactStore.search()`，不再有两套打分。

## 3. 写入路径

### 3.1 聊天内“记住…”

用户说：

> 记住：项目周五交付第一版，负责人小林。

`ChatAgent` 识别为 memory 请求，生成一条 `save_memory` 待确认动作卡。确认后才写入；不会静默写。支持“记住这件事/刚才”从最近对话取上下文。类型自动判断为 action-item / decision / commitment / risk / open-question / preference / project-fact。

### 3.2 会后自动候选

会议结束（聊天里点“结束会议”或录音按钮退出）后：

- 先跑**无模型快速提取**：扫描决定/承诺/风险/未决/行动语言，带 turn 证据，最多 8 条；
- 在对话里自动出现“写入本次会议 N 条记忆”的待确认卡；
- 用户确认后逐条重验证据再写入；
- Agent 页仍保留 LLM 深度提取（预览→勾选→写入），两者不冲突。

快速提取的意义是把“会后沉淀”从需要用户自己找 Agent 页，变成默认出现在对话里；它保守、可解释，不假装是完整语义抽取。

### 3.3 幂等、版本与合并

- 同名事实再次写入 → 版本 +1，旧版本进 `versions/`；
- 同一 `canonical_key` 不同标题 → 新事实生效，旧事实标记 `superseded`；
- 来源会议累计到 `source_meetings`，`source_count` 用于排序；
- 事实名称仍由 sanitized slug 生成，路径穿越/符号链接防护保留。

## 4. 冲突队列

写入新事实时，若同类型已有高重叠事实，并且出现以下任一冲突信号：

- 负责人不同（双方都非空）；
- 截止不同（双方都非空）；
- 一边有否定/取消/延期词，另一边没有；
- 双方都有日期且日期不同；

新事实不会直接生效，而是进入 `pending-conflict`：

- 默认 `search` 只返回旧的有效事实，不把冲突新事实混进回答；
- `MEMORY.md` 有“待确认冲突”区；
- UI 可“采用新事实 / 保留旧事实 / 合并”；
- 采用新事实会把旧事实标 `superseded`；合并会保留双方证据和来源；
- 冲突处理会递增版本并写快照。

这是确定性启发式，不是语义定理证明；它宁可把可疑变化放进待确认队列，也不静默覆盖旧决定。

## 5. 对话记忆

- 每个对话 JSON 新增 `summary` 和 `summary_until`；
- 每累计 12 条消息生成一次抽取式滚动摘要，保留最近 1800 字；
- `_history` 给模型的是“滚动摘要 + 最近 8 条”，不再只有最近 10 条；
- 新增 `search_conversations` / `read_conversation` 工具，支持跨对话回忆；
- 最终答案可引用 `{"kind":"conversation","conversation_id":"..."}`，引用校验器会检查该对话是否真的被工具返回过；
- 对话仍是项目作用域，不能跨项目读取。

## 6. 记忆治理 UI

Agent 页的“项目记忆”卡现在支持：

- 关键词检索 + 状态筛选（生效中/待确认冲突/已被替代/已过期/全部）；
- 显示类型、状态、版本、来源会议、相关度、命中词；
- 编辑描述/正文/状态；
- 置顶/取消置顶；
- 软删除（保留审计；数据页的整项目删除仍是硬删除）；
- 冲突三选一按钮；
- 状态行显示生效事实数、类型分布和待确认冲突数。

## 7. 40 题记忆基准

`benchmarks/memory/memory_benchmark.json` 是纯合成数据，不含任何真实会议或用户内容。22 条事实、40 个问题，覆盖：

- 15 个直接/同义查询；
- 5 个类型过滤；
- 3 个冲突查询 + 冲突队列检查；
- 5 个过期/被替代事实的“不应出现在默认视图”；
- 5 个负对照（财务/年会/座位/团建/食堂）；
- 7 个偏好、未决、风险、客户等主题查询。

运行：

```bash
python scripts/memory_benchmark.py \
  --json memory_benchmark_report.json \
  --md memory_benchmark_report.md
```

本轮结果：

| 指标 | 结果 |
|---|---:|
| 问题数 | 40 |
| recall@5 | **1.000** |
| top1 accuracy | **0.852** |
| citation precision@1 | **0.852** |
| 负对照准确率 | **1.000** |
| 过期事实默认视图干净 | **1.000** |
| 过期事实可显式恢复 | **True** |
| 冲突被识别 | **True** |
| 冲突新事实不进入默认检索 | **True** |
| 旧事实在冲突期间仍生效 | **True** |
| 命中原因覆盖 | **0.825** |

top1 的 4 个未命中都是“同一查询有多个合理事实”（例如供应商交付与项目整体交付），不是答非所问；recall@5 全部命中。这个基准是合成回归集，不等于真实用户记忆质量，也没有人工相关性标注。

## 8. 兼容与迁移

- 旧版事实文件不需要手动迁移：`_ensure()` 发现 `facts/*.md` 但索引为空时自动重建；缺失的 status/canonical 字段按 `active` 读取；
- `FactStore.index()` 保留为 `MEMORY.md` 文本入口，旧 CLI/测试不破坏；
- `MEMORY.md` 仍是可重建索引，不是事实源；
- SQLite 索引损坏时删除 `memory.sqlite3` 即可重建，Markdown 事实不受影响。

## 9. 边界

- 没有向量 embedding；同义表达靠 bigram + 少量业务同义词，跨领域同义仍需后续加 embedding/reranker。
- 对话摘要是抽取式，不是 LLM 语义摘要；长对话会保留尾部，早期细节可能被压缩。
- 冲突检测是启发式，可能漏掉隐喻式矛盾，也可能把“正常改期”放进待确认队列；这是有意的安全偏向。
- 软删除事实仍在磁盘上，整项目“删除数据”才会硬删。
- 40 题基准是合成数据，尚未用真实用户 30 天记忆做在线评测。
- 记忆 UI 已在源码和 0.11.0 安装包中；真实设备上的视觉验收仍需按发布流程跑一次。