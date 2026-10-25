# 知识图谱 Phase 2–4 完成记录（2026-10-25）

## Phase 2：GraphRAG

新增 `src/meetingvar/kg/graphrag.py`：

```text
query
 → 实体链接（精确/别名/FTS/BGE 语义）
 → 种子节点
 → 1–2 跳子图（active + 时间窗口）
 → 边证据（chunk/transcript/fact）
 → 项目 RAG chunk（FTS+BGE+rerank）
 → 图关系 + 原文一起给模型
 → [G1] 关系引用 / [K1] 资料引用
```

支持：

- `as_of` 时间点查询；
- 时间性问题（之前/上次/改口/变化）自动加历史边；
- 边证据优先取 `chunk_id`，其次 session+turn_ids，最后 FactStore；
- GraphRAG 结果中图关系和 RAG chunk 都保留，避免“只有图没有原文”；
- Agent 工具 `search_graph`；
- CLI `kg-search` / `kg-ask`；
- 引用校验新增 `kind="node"` / `kind="edge"`，仍保留 document/fact/turn/conversation。

真实本地 Agnes 2.5 实测：

```text
问题：谁负责周五交付第一版？
回答：根据现有图谱和资料，负责人是小林 [G2]/[K1]。
耗时：2.5s
引用：edge + document
```

## Phase 3：图谱 UI

新增“知识图谱”侧栏页：

- Cytoscape.js 离线包（`desktop/web/vendor/cytoscape.min.js`，MIT）；
- 节点颜色按类型；边显示关系、状态（asserted/pending/superseded/retracted）；
- 查询框 + 1/2/3 跳 + as-of 日期；
- 点击节点：详情 + 时间线 + 相关边 + 证据 quote；
- 点击边：有效期、状态、来源会议、quote、确认/驳回；
- “规则构图”（不调 LLM，保留已有 LLM 边）；
- “LLM 抽取当前会话”（Agnes，按会话选择）；
- “合并选中实体”（先点源、再点目标）；
- “GraphRAG 问答”显示答案和引用；
- 关系审核写回 `kg_review_edge`，实体合并写回 `kg_merge_nodes`。

## Phase 4：评测与压测

### GraphRAG A/B（`scripts/kg_eval.py`）

合成项目：1 meeting + 1 requirement doc + facts；检查项 9 个（RAG-only 作为基线，不计入通过率）：

| 检查 | 结果 |
|---|---|
| 实体别名词典（林工→小林） | PASS |
| assigned_to 邻居 | PASS |
| Action→Project 路径 | PASS |
| due_on 时间线（新旧两条） | PASS |
| 时间失效（当前只剩周一） | PASS |
| GraphRAG 图证据召回 | PASS |
| GraphRAG 原文召回 | PASS |
| RAG-only 边召回（基线） | FAIL（预期，RAG 无图边） |
| GraphRAG 引用 | PASS |

指标：

- checked 8 / passed 8 / pass_rate 1.0；
- GraphRAG 检索约 198ms（本地 BGE+FTS，不含 LLM 回答）；
- 9 节点 / 13 边。

这说明：**RAG 单独不能给出关系边；GraphRAG 能同时拿到关系和原文。**

### 规模压测（`scripts/kg_scale_test.py`）

| 规模 | 导入 | 邻居 p50/p95 | 路径 p50/p95 | DB |
|---|---:|---:|---:|---:|
| 10k nodes / 100k edges | 2.26s | 42.8/46.4ms | 56.2/61.9ms | 30.8MB |
| 10k nodes / 1M edges | 38.4s | 57.1/63.4ms | 113.0/120.0ms | 297MB |
| 100k nodes / 1M edges | 27.1s | 42.3/47.1ms | 55.6/61.8ms | 317MB |

结论：SQLite 属性图在 10 万节点/100 万边规模下，2 跳邻居和 3 跳路径仍低于 150ms，满足桌面产品交互需求。

### 真人抽取评测

`scripts/kg_human_eval.py`：

```bash
python scripts/kg_human_eval.py --project "D:/项目/X" --export kg_labels.csv
python scripts/kg_human_eval.py --project "D:/项目/X" --labels kg_labels.csv --out kg_eval.json
```

人工填 `head_correct/tail_correct/relation_correct/evidence_supports`，计算 head/tail/relation accuracy、证据支持率、triple precision。

**没有真人标签时不生成“准确率”；不伪造。**

## 反复打磨：抽取稳定性

真实长会（1185 turns，69 chunks）用 Agnes 2.5：

- 无节流时约 10 次调用后进入 `rate_limit`，59/69 块失败；
- 已加节流（默认 2 秒/块）+ `rate_limit/network` 指数退避重试（5/10/20/40 秒）；
- 节流后整场 **583 秒** 完成，69/69 chunks 有 episode，35 个实体、0 条关系、2 个失败；
- 前 10 chunks 抽样（加端点推断后）15 实体/3 关系；整场 0 关系说明这段内容是长独白/课堂，不是关系密集会议，**不能据此报关系准确率**；
- 失败的 chunk 不写 episode，可重跑；
- 本地 Qwen3-4B 不再作为生产抽取默认，Agnes 2.5 是当前默认。

## 边界

- 实体/关系质量仍未达到“20 场会议人工 P/R”门槛；已有导出/计分工具，但真实标签需要人；
- 3.0 Flash 质量更高但约 51–93 秒/块，适合高价值会议，不适合全量；
- GraphRAG 的 LLM 回答依赖已配置模型；检索本身可离线；
- UI 视觉已做静态渲染检查，仍需在真实安装版上做一次人工点击验收；
- 图谱是投影，事实/转写仍是事实源；`kg-build --reset` 会清空 LLM 边，默认不 reset。