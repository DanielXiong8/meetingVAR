# 知识图谱 Phase 0 落地记录（2026-10-25）

## 已完成

Phase 0 目标：不调用 LLM，仅用现有 FactStore 事实 + 会议/说话人规则构图，验证图查询是否值得继续做。

### 1. SQLite 图存储

新增：

```text
src/meetingvar/kg/schema.py
src/meetingvar/kg/store.py
src/meetingvar/kg/build.py
```

数据库：

```text
<项目>/meetingvar/kg/graph.sqlite3
```

表：

- `kg_nodes`：实体、类型、别名、状态、置信度；
- `kg_edges`：关系、`valid_from/valid_to`、`observed_at`、状态、来源、turn_ids、quote；
- `kg_episodes` / `kg_aliases`：预留 provenance 与实体别名；
- `kg_nodes_fts`：中文 bigram FTS5。

### 2. 规则建图

`GraphBuilder` 从现有数据构图：

- Project 节点：项目目录名；
- Meeting 节点：`data/pool` 中的已保存会话；
- Person：事实 owner、会议里非 `spk0` 的具名说话人；
- Decision/Action/Commitment/Risk/OpenQuestion/Topic：来自 FactStore；
- 边：
  - `part_of`：事实/会议 → 项目；
  - `derived_from`：事实 → 会议；
  - `decided_in`：决定 → 会议；
  - `assigned_to` / `owned_by`：任务/承诺/决定 → 负责人；
  - `due_on`：任务/承诺/决定 → 日期；
  - `speaks_in`：具名说话人 → 会议。

事实的 `valid_from` 优先使用来源会议的 mtime；过期/替代事实保留 `valid_to`，默认邻居查询按当前时间过滤。

### 3. 三个查询 + 统计

`GraphStore`：

- `find_nodes(query, kind?)`：FTS + 精确别名；
- `resolve(query)`：先精确名称/别名，再 FTS；
- `neighbors(node_id, depth=1)`：BFS，支持方向/关系过滤、当前有效边；
- `timeline(node_id)`：按 `valid_from` 排序列出全部关系（含历史）；
- `path(src,dst,max_depth=3)`：最短路径；
- `stats()`：节点/边/类型分布。

### 4. CLI

```bash
python -m meetingvar kg-build --project "D:/项目/X"
python -m meetingvar kg-build --project "D:/项目/X" --no-meetings --json

python -m meetingvar kg-find --project "D:/项目/X" --query "小林"
python -m meetingvar kg-neighbors --project "D:/项目/X" --query "周五交付第一版" --depth 2
python -m meetingvar kg-timeline --project "D:/项目/X" --query "采用第二方案"
python -m meetingvar kg-path --project "D:/项目/X" --src "周五交付第一版" --dst "小林"
python -m meetingvar kg-stats --project "D:/项目/X"
```

### 5. Agent 工具

已注册只读工具：

- `kg_find_entities`
- `kg_neighbors`
- `kg_timeline`
- `kg_path`
- `kg_stats`

Agent prompt 增加：关系/时间/路径问题先用 `kg_find_entities`，再 `kg_neighbors/kg_timeline/kg_path`；图空时明确说明并退回会议/记忆检索。

## 验证

新增 5 项 KG 测试：

- 规则建图节点/边统计；
- 精确实体解析；
- 邻居与路径（Action → Person / Project）；
- 时间线与过期边过滤；
- 重建幂等 + Agent 工具注册。

当前结果：

```text
Ran 5 tests
OK
```

示例图（测试项目）：

```text
Project 星河项目
Meeting 会议 s1
Person 小林 / 小王
Decision 采用第二方案
Action 周五交付第一版
Risk 测试时间不足
Date 周五

Action --assigned_to--> 小林
Action --due_on--> 周五
Action --part_of--> 星河项目
Action --derived_from--> 会议 s1
Decision --decided_in--> 会议 s1
Person --speaks_in--> 会议 s1
```

## Phase 0 的边界

- 只做规则建图：owner 来自事实元数据/正文正则，会议说话人只取具名 speaker；`spk0` 不建 Person；
- 不做 LLM 实体关系抽取；
- 不做实体合并 UI、不做图谱可视化、不做 GraphRAG；
- 图是可重建投影，事实/转写仍是事实源；
- 过期事实节点仍在图中（用于历史），但默认邻居查询不返回已失效边。

## 下一步 Phase 1

1. 会议 chunk → 严格 JSON 实体/关系抽取；
2. 实体链接：别名 + BGE + LLM 灰区复核；
3. 边证据校验（turn_id/quote）；
4. 20 场会议人工抽查实体/关系 P/R；
5. 若 P/R 达标，再接 GraphRAG（Phase 2）。