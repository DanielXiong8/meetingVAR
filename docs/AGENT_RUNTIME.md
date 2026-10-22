# meetingVAR Agent Runtime（Phase 0 + 本地 Agent 闭环）

本模块借鉴 ZCode（Apache-2.0）的**受控 Agent 运行时**思想，但不复制其编码 IDE 工具。实现保持独立、纯标准库、可离线测试。

> 0.3.0 起新增模型驱动的企业级 Agent Runtime v2（原生工具调用、有界 ReAct、Principal/审批、LLM 级 Trace、断点续跑、黄金集评测）。见 [Agent Runtime v2](AGENT_RUNTIME_V2.md)。本文保留 0.2.x 的确定性工作流说明。

## 已实现

### 1. 工具契约（`agent/tools.py`）

每个工具声明：

- 输入 JSON Schema（强制校验，拒绝未知字段）
- 只读/写入属性、副作用范围（`none/meeting/project/external`）
- 是否需要审批、超时、输出字符上限
- 幂等缓存键

执行流程：校验参数 → 审批（缺失/拒绝即失败）→ 超时执行 → 结果限长 → Trace。审批异常也按拒绝处理，fail-closed。

### 2. 任务状态机（`agent/runtime.py`）

状态：`pending → running → waiting_approval → completed/failed/cancelled`。

每一步写检查点，包含步骤数、最后工具、错误码和摘要；支持加载恢复。步骤预算耗尽会失败，不会无限循环。运行时不运行自由模型循环，由确定性流程或规划器决定调用哪个工具。

### 3. 项目记忆（`agent/memory.py`）

- 一个事实一个 Markdown 文件，带 frontmatter（name/description/metadata）。
- 元数据含 type、scope、origin_meeting、source_refs、confidence、version、时间，以及 0.11.0 的 status、valid_from/valid_to、canonical_key、supersedes、conflict_with、source_meetings、pinned/tags。
- `MEMORY.md` 是**可重建索引**，不是事实源；`memory.sqlite3` 是可删除的 FTS5 检索索引。
- 原子写入（临时文件 + `os.replace`），路径穿越/符号链接拒绝；同名更新版本递增并保留 `versions/<slug>/vN.md` 快照。
- 生命周期：active / superseded / expired / pending-conflict / rejected / deleted；冲突新事实默认不进入检索，必须由用户“采用新/保留旧/合并”。
- 事实类型：`action-item/decision/commitment/project-fact/preference/risk/open-question`。
- 召回：中文 bigram + BM25，返回 score/why；业务同义词、类型/状态/来源过滤。
- 写入入口：会后事实审批、聊天“记住…”审批卡、会后快速候选；模型仍不能自由写。

### 4. Trace（`agent/trace.py`）

本地 append-only JSONL，按大小轮转。只记录工具名、参数摘要、状态、耗时、错误码和结果长度；不记录提示词、转写正文或密钥。敏感字段名自动 `[redacted]`。

### 5. 会后会议事实提取（`agent/postmeeting.py`）

流程：

```text
读取 transcript.json（turn_1...）
→ 按字符预算分块，模型提取候选事实与 evidence_turn_ids
→ 本地校验 ID/字段，合并重复标题
→ get_transcript_window 只读核对原句
→ 用户审批（--yes 可自动化，--dry-run 只核对）
→ create_meeting_fact 幂等写入项目记忆
```

支持五类事实：`action-item`、`decision`、`commitment`、`risk`、`open-question`。模型只提出候选和证据 ID；**原句由本地工具回填**，不信任模型复述。审批拒绝、证据不存在、LLM 失败都不会写入。

### 6. 项目记忆问答（`agent/recall.py`）

`agent-ask` 先走统一 `FactStore.search()`（FTS5/BM25 + 同义词/过滤），失败时退回关键词扫描，只把命中的事实交给模型；模型回答必须用 `[事实名称]` 引用，未提供的引用会被标记。无事实时不调用模型。

### 7. 会前关注清单（`agent/focus.py`）

`agent-focus` 读取项目可读材料，生成 3–8 条“可判断是否发生”的关注点，审批后保存到：

```text
<项目>/meetingvar/agent_memory/focus.json
```

下一次 `go` 的项目上下文会读取它，API 兴趣判断可据此理解“本场该关注什么”。`--dry-run` 不落盘。

## 桌面 Agent 页（0.2.1）

左侧导航新增 **Agent** 页，当前项目下可：

- **会前关注清单**：生成 → 勾选 → 保存到 `focus.json`；生成失败或模型未配置会明确提示。
- **会后会议事实**：选择已保存会话 → 预览五类事实与原句证据 → 勾选 → 写入项目记忆。预览不落盘，写入必须由用户勾选确认。
- **项目记忆问答**：输入问题，只依据已保存事实回答并显示引用；没有依据会明确说明。
- **项目记忆**：按关键词查看/检索事实、类型、来源会议。

后端通过 `desktop/engine_bridge.py` 的 `agent_*` 命令调用引擎 Python，继续复用 `MEETINGVAR_LLM_*` 和同一套 Agent 工具/审批/Trace。桌面 UI 不直接 import 引擎核心。

## 使用

```bash
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目" --dry-run
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目"       # 逐条审批
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目" --yes --json

meetingvar agent-ask --project "D:\项目\某项目" --question "第二方案定了吗？" --json
meetingvar agent-focus --project "D:\项目\某项目" --dry-run
meetingvar agent-focus --project "D:\项目\某项目" --yes
meetingvar agent-memory --project "D:\项目\某项目" --query "第二方案"
```

写入位置：

```text
<项目>/meetingvar/agent_memory/MEMORY.md
<项目>/meetingvar/agent_memory/facts/*.md
<项目>/meetingvar/agent_memory/focus.json
<项目>/meetingvar/agent_memory/trace/<session>.jsonl
<项目>/meetingvar/agent_memory/trace/tasks/*.json
```

运行需要 `transcript.json`（会后事实提取）和已配置的 `MEETINGVAR_LLM_*`。`--json` 必须配合 `--yes` 或 `--dry-run`，避免无人值守时等待输入。

## 验证

- 37项专项测试：Schema 拒绝、审批失败/拒绝、超时、幂等缓存、Trace 脱敏、状态检查点、步骤预算、事实原子写/索引重建/路径清洗、五类事实核对、审批拒绝、dry-run、LLM失败不写、记忆问答引用、会前关注清单、上下文裁剪、本地上下文自动重试、CLI。
- 真实外部API合成会议演练：2个候选、2个经证据核对并写入项目事实，`MEMORY.md` 与证据原句生成；这是合成内容，不含真实会议，也不代表行动项质量已验收。
- 未调用真实模型或网络；测试使用纯假 LLM/临时目录（上面单独的真实API演练除外）。
- 0.2.1 真实窗口复测：本地 16K Qwen3-4B 生成 7 条/4 条关注清单并保存，重新进入页面仍能读取；会话下拉只显示有 `transcript.json` 的 19 场；0 条事实时问答不调用模型并明确显示无依据；停掉本地服务后预览事实显示“本地模型服务未启动”。
- 0.2.2 全库理解容错：严格 JSON 失败后依次尝试单尾部引号清理、未转义内层引号修复、两次低温重试、精简关键词模式；仍失败的可恢复块跳过并继续。真实 UI 冷启动 `D:\项目\数据结构` 读到 14/14 块、100%，密集合块自动降级；仅 `ok && complete` 才发布项目上下文。

## 边界与下一步

- 当前只做**会后**流程，不接管会中采集/保存；现有快路径不变。
- 未实现外部连接器（邮件/日历/LMS）、RBAC/多租户、加密审计存储；这些需要单独设计和评审。
- `ThreadPoolExecutor` 超时是协作式限制，超时线程可能继续执行；写工具已用原子写+幂等键，但真正的进程级取消仍需后续实现。
- 下一步候选：项目记忆检索工具、决策/承诺提取、会前关注清单 Agent，以及用真实会议回放做工具调用/审批/证据引用的评测。

## ZCode 参考边界

借鉴了其工具元数据、审批 fail-closed、任务/会话持久化、事实文件+索引、Trace 与遥测分离等模式；未复制 Bash/Edit/Read 文件状态、Explore 子代理 yolo、Electron/SSH 编排等编码 IDE 专属实现。ZCode 为 Apache-2.0；如未来直接复用代码，必须保留 LICENSE/NOTICE 并标注修改。