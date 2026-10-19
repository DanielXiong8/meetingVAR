# meetingVAR Agent Runtime（Phase 0 + 会后行动项）

本模块借鉴 ZCode（Apache-2.0）的**受控 Agent 运行时**思想，但不复制其编码 IDE 工具。实现保持独立、纯标准库、可离线测试。

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
- 元数据含 type、scope、origin_meeting、source_refs、confidence、version、时间。
- `MEMORY.md` 是**可重建索引**，不是事实源。
- 原子写入（临时文件 + `os.replace`），路径穿越/符号链接拒绝，同名更新版本递增。
- 事实类型：`action-item/decision/commitment/project-fact/preference/risk/open-question`。

### 4. Trace（`agent/trace.py`）

本地 append-only JSONL，按大小轮转。只记录工具名、参数摘要、状态、耗时、错误码和结果长度；不记录提示词、转写正文或密钥。敏感字段名自动 `[redacted]`。

### 5. 会后行动项 Agent（`agent/postmeeting.py`）

流程：

```text
读取 transcript.json（turn_1...）
→ 按字符预算分块，模型提取候选行动项与 evidence_turn_ids
→ 本地校验 ID/字段，合并重复标题
→ get_transcript_window 只读核对原句
→ 用户审批（--yes 可自动化，--dry-run 只核对）
→ create_action_item 幂等写入项目记忆
```

模型只提出候选和证据 ID；**原句由本地工具回填**，不信任模型复述。审批拒绝、证据不存在、LLM 失败都不会写入。

## 使用

```bash
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目" --dry-run
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目"       # 逐条审批
meetingvar agent-actions --session go_xxx --project "D:\项目\某项目" --yes --json
```

写入位置：

```text
<项目>/meetingvar/agent_memory/MEMORY.md
<项目>/meetingvar/agent_memory/facts/*.md
<项目>/meetingvar/agent_memory/trace/<session>.jsonl
<项目>/meetingvar/agent_memory/trace/tasks/*.json
```

运行需要 `transcript.json`（有命中片段保存的会话）和已配置的 `MEETINGVAR_LLM_*`。`--json` 必须配合 `--yes` 或 `--dry-run`，避免无人值守时等待输入。

## 验证

- 22项专项测试：Schema 拒绝、审批失败/拒绝、超时、幂等缓存、Trace 脱敏、状态检查点、步骤预算、事实原子写/索引重建/路径穿越、行动项证据核对、审批拒绝、dry-run、LLM失败不写、CLI。
- 真实外部API合成会议演练：2个候选、2个经证据核对并写入项目事实，`MEMORY.md` 与证据原句生成；这是合成内容，不含真实会议，也不代表行动项质量已验收。
- 未调用真实模型或网络；测试使用纯假 LLM/临时目录（上面单独的真实API演练除外）。

## 边界与下一步

- 当前只做**会后**流程，不接管会中采集/保存；现有快路径不变。
- 未实现外部连接器（邮件/日历/LMS）、RBAC/多租户、加密审计存储；这些需要单独设计和评审。
- `ThreadPoolExecutor` 超时是协作式限制，超时线程可能继续执行；写工具已用原子写+幂等键，但真正的进程级取消仍需后续实现。
- 下一步候选：项目记忆检索工具、决策/承诺提取、会前关注清单 Agent，以及用真实会议回放做工具调用/审批/证据引用的评测。

## ZCode 参考边界

借鉴了其工具元数据、审批 fail-closed、任务/会话持久化、事实文件+索引、Trace 与遥测分离等模式；未复制 Bash/Edit/Read 文件状态、Explore 子代理 yolo、Electron/SSH 编排等编码 IDE 专属实现。ZCode 为 Apache-2.0；如未来直接复用代码，必须保留 LICENSE/NOTICE 并标注修改。