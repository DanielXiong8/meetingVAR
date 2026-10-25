# 知识图谱 Phase 1 落地记录（2026-10-25）

## 已完成

Phase 1 = LLM 实体/关系抽取 + 实体链接 + 会后增量入图。

### 1. 严格 JSON 抽取

新增：

```text
src/meetingvar/kg/extract.py
src/meetingvar/kg/link.py
src/meetingvar/kg/ingest.py
```

流程：

```text
session transcript
 → 按 turn 分块（约 900 字，保留 turn_id/时间戳）
 → LLM 输出 entities/relations JSON
 → 类型/关系白名单校验
 → evidence turn_id 必须存在
 → surface/head/tail 必须出现在证据原句
 → 关系 domain/range 校验
 → 实体链接 / 新建节点
 → 边写 SQLite 图 + episode 去重
```

### 2. 关系 domain/range

`kg/schema.py` 增加 `RELATION_ALLOWED`，例如：

- `assigned_to`: Action/Commitment → Person
- `due_on`: Action/Commitment → Date
- `decided_in`: Decision → Meeting
- `derived_from`: Decision/Action/Commitment/Risk/OpenQuestion → Meeting/Document
- `blocks`: Risk → Action/Project/Risk
- `speaks_in`: Person → Meeting

错误组合（例如 Person --assigned_to--> Decision）在解析和入库两层都会被丢弃。

### 3. 实体链接

`EntityLinker`：

1. 精确名称/别名；
2. 同 kind 内 BGE 余弦 ≥0.88；
3. 0.65–0.88 可调用 LLM 二选一；
4. 仍不确定 → 新建节点（标记 `provisional`）。

不会把 Person 合并到 Project，也不会因为名字相似就静默合并。

### 4. 增量与幂等

每块是一个 episode：

```text
meeting:<session_id>:<chunk_index>
content_hash = sha256(chunk text)
```

重复运行同一会话会跳过相同 chunk；`--force` 才重抽。抽取失败不写 episode hash，下次可重试。

### 5. CLI

```bash
python -m meetingvar kg-ingest --project "D:/项目/X" --session s1
python -m meetingvar kg-ingest --project "D:/项目/X" --all --limit 20
python -m meetingvar kg-ingest --project "D:/项目/X" --session s1 --force --json
```

原有查询：

```bash
python -m meetingvar kg-find --project ...
python -m meetingvar kg-neighbors --project ... --query ...
python -m meetingvar kg-timeline --project ... --query ...
python -m meetingvar kg-path --project ... --src ... --dst ...
```

### 6. 会后联动

`ChatAgent` 在会议事实写入成功后，会 best-effort 重建规则图（不调 LLM）：Decision/Action/Risk/负责人/会议/项目边立即进入图。LLM 深度抽取由 `kg-ingest` 显式运行，避免每次结束会议都阻塞数分钟。

## 验证

新增/更新测试：

- 从会议抽取并按 domain/range 过滤；
- 实体链接到已有 Person/Decision；
- 无效 evidence turn 被拒绝；
- 重复 ingest 跳过；
- Phase0 的邻居/时间线/路径仍通过。

完整测试：**533 项通过，0 跳过（74.15 秒）**。

### 本地 Qwen3-4B 实测

两句话的测试会议：

```text
我们决定采用第二方案，由小林负责，周五交付第一版。
测试时间不足是一个风险，可能影响交付。
```

- 未加 `/no_think`：输出 1800 tokens 被截断，抽取失败；
- 加 `/no_think` + 类型/关系约束：4–5 个实体，1 条有效关系，约 5–7 秒。

**结论：管道、校验、链接、幂等都已可用；本地 4B 的关系抽取质量有限，生产抽取建议用更强的本地模型或已配置的云端模型。** Phase0 的规则图仍然是可靠底座。

## 边界

- 本地 4B 容易把类型名当实体名，已用 prompt 约束 + synthetic “本次会议/本项目”端点缓解，但仍需更强模型；
- 关系抽取 P/R 尚未达到 Phase1 目标，需要 20 场会议人工标注后再判断是否进入 Phase2 GraphRAG；
- 不做跨会议实体合并 UI；实体合并目前只靠精确/别名/BGE/可选 LLM；
- LLM 抽取是显式 CLI，不默认在每次会议结束时自动跑。

## 下一步 Phase2

只有满足以下条件才做 GraphRAG：

1. 20 场会议抽样，实体 precision ≥0.80、关系 precision ≥0.75；
2. 实体错误合并率 ≤0.03；
3. 时间线/失效正确率 ≥0.90。

否则优先加规则抽取和人工校正，不堆 GraphRAG。