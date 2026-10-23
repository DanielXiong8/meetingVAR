# RAG 与向量记忆（0.12.0，2026-10-23）

## 1. 新增了什么

- **本地中文 embedding**：`BAAI/bge-small-zh-v1.5`，CPU 编码，512 维；
- **记忆混合检索**：FTS5/BM25 + BGE 向量余弦，按 `score` 排序并返回 `why`；
- **LLM reranker**：对记忆/RAG 的候选做 listwise 重排；模型不可用时退回 BGE 余弦；
- **项目 RAG**：项目文档 + 已保存会议转写 → 分块 → FTS5 + 向量 → 重排 → 带来源回答；
- **RAG 工具/CLI/UI**：Agent 可用 `search_project_knowledge`/`read_project_chunk`，桌面 Agent 页有“项目知识库 RAG”卡片；
- **LLM 语义摘要**：对话每 12 条消息优先用模型压缩为长期摘要，失败时退回抽取式；
- **LLM 冲突裁判**：确定性信号之外，再让模型判断新旧事实是否真的矛盾；
- **硬删除**：记忆 UI 区分“删除（软删除）”和“彻底删除（文件+版本快照）”。

## 2. Embedding 模型

模型下载位置（本机）：

```text
D:\env\models\modelscope\models\BAAI--bge-small-zh-v1.5\snapshots\master
```

查找顺序：

1. `MEETINGVAR_EMBEDDING_MODEL`（目录或模型名）；
2. `MEETINGVAR_MODELS`/`runtime/models` 下含 `bge-small-zh`、`bge-base-zh`、`bge-m3` 的目录；
3. 找不到则只用 FTS5，功能不中断。

模型缺失、transformers/torch 不可用、加载失败都会自动降级；不会阻塞转写/会议流程。

## 3. 记忆混合检索

`FactStore.write()` 在写 Markdown 后：

- 更新 FTS5；
- 若 embedding 可用，编码 `name + description + body`，写入 `fact_vectors`；
- 旧事实的向量缺失时，首次检索自动补齐。

`FactStore.search()`：

- FTS5 BM25 候选；
- BGE 查询向量与事实向量余弦；
- 合并分 = BM25 + 类型/新近/置信/来源/置顶 + 余弦；
- 只有语义命中（无词法命中）时要求余弦 ≥ 0.48，避免“财务报销”匹配“服务器预算”这类噪声；
- 返回 `_score`、`_why`（命中词/语义）。

当前 40 题基准：recall@5 1.000、top1/citation precision@1 0.852、负对照 1.000、过期默认视图 1.000。

## 4. Reranker

`agent/rerank.py`：

- 有配置模型时：把 query + top 候选编号发给模型，要求 `{"ranking":[...]}`；只重排已有候选，不生成新事实；
- 模型未配置/失败/JSON 无效：用 BGE query-candidate 余弦重排；
- 仍失败则保留混合排序。

RAG 的 `search()` 默认启用；记忆 `FactStore.search()` 保留混合排序，记忆问答在模型可用时可扩展为 LLM rerank。

## 5. 项目 RAG

索引：

```text
<项目>/meetingvar/rag/index.sqlite3
```

来源：

- 项目内可读文档（沿用 `memory/files.py` 的内容优先提取：PDF/DOCX/PPTX/旧 Office/RTF/文本/代码）；
- `data/pool/<session>/transcript.json` 已保存会议转写；chunk 保留 turn_ids、start/end 时间戳、speaker。

分块：

- 文档约 850 字符，段落优先，超长段落滑窗重叠 120 字符；
- 会议按 turn 累积到约 850 字符，记录 turn 范围和时间；
- 文档/会话内容 hash 不变则跳过；`--force` 强制重建。

检索：

- FTS5 中文 bigram + BGE 向量；
- 可选按 `source_type=document|meeting` 过滤；
- 重排后返回 chunk_id、来源、标题、路径、时间戳、分数。

回答：

- `RagIndex.answer()` 把 top 片段编号为 `[K1]`…，要求模型只依据片段回答并引用；
- 引用映射为 `kind="document"`，带 chunk_id/source_id/title/path/时间戳；
- Agent Loop 的引用校验也接受 document citation，并核对 chunk 是否真的由工具返回过。

## 6. 入口

CLI：

```bash
python -m meetingvar rag-index --project "D:/项目/某项目"
python -m meetingvar rag-index --project "D:/项目/某项目" --no-meetings
python -m meetingvar rag-ask --project "D:/项目/某项目" --question "客户要求了什么？"
```

Agent 工具：

- `search_project_knowledge(query, source_type?, limit?)`
- `read_project_chunk(chunk_id)`

桌面 Agent 页：

- “项目知识库 RAG”卡片：建立/刷新索引、强制重建、状态（来源数/片段数/是否启用向量）、提问与引用展示。

## 7. 当前验证

- 完整测试：524 项通过，0 跳过。
- RAG 专项：文档/会议索引、增量跳过、强制重建、chunk 读取、回答引用。
- Agent 引用：document citation 通过校验。
- 记忆基准：40 题合成 + 30 天纵向模拟。

## 8. 边界

- RAG 只处理可提取文本；扫描件图片/复杂表格没有 OCR/版面重建；
- 会议 RAG 只索引已保存到 `data/pool` 的转写，未保存的会议不索引；
- BGE 是中文小模型，跨语言/极长文档仍有限；
- LLM rerank 依赖已配置模型；本地模型会增加延迟；
- 30 天纵向目前是模拟；真人 30 天数据需要按 `docs/MEMORY_HUMAN_EVAL.md` 采集标注，不能伪造；
- 索引是本机可重建产物，不随源码包分发。