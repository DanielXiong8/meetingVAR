# 企业级 Agent Runtime v2（0.3.0）

日期：2026-10-19。本文说明 meetingVAR 0.3.0 的模型驱动 Agent 架构、工具边界、安全策略、可观测性、评测和已知边界。确定性的会前关注/会后事实流水线仍然保留，不要求“所有任务都变成自由 Agent”。

## 1. 定位

系统现在有两条路径：

| 路径 | 控制者 | 用途 |
|---|---|---|
| Controlled Workflow | 代码 | 会前关注、会后五类事实、事实写入；要求稳定、可审计、低成本 |
| Agent Runtime v2 | 模型 + 应用边界 | 开放式跨会检索、对比、定义/规则/承诺核对；步骤无法预先枚举 |

Agent Runtime v2 不是“让模型随意跑”。模型只能从应用注册的只读工具中选择，权限、预算、停止条件、引用校验和状态持久化都由应用拥有。

## 2. 核心循环

```text
Question
  → Planner（结构化 JSON 计划，最多 5 步）
  → RAG prefetch（定义/规则类问题，确定性检索候选 turn）
  → ReAct loop:
       model turn
         ├─ native tool_call → 应用校验权限/参数 → 执行工具 → observation → 下一轮
         ├─ JSON action tool → 同上（无原生 function calling 的兼容路径）
         └─ final → schema + citation 校验 → 完成 / 纠正一次
  → 持久化 task/messages/observations/usage
```

关键代码：`src/meetingvar/agent/loop.py`。

### 2.1 原生工具调用优先，JSON action 兜底

- `llm.chat_messages()` 发送 OpenAI 兼容的 `tools` / `tool_choice`，解析 `message.tool_calls`。
- 本地 llama-server 不支持工具时，HTTP 400/422 会自动切换到 JSON action 协议：
  - `{"action":"tool","tool":"search_sessions","arguments":{...}}`
  - `{"action":"final","answer":"...","citations":[...]}`
- 两条路径都是真正的 ReAct：模型看到 observation 后再决定下一步。

### 2.2 停止条件

- 最大步数（默认 8，桌面最多 12）
- 最大工具调用数（默认 12）
- 最大输入/输出 token
- 最大墙钟时间（默认 180 秒）
- 连续三次工具无证据（`no_progress`）
- 同一工具调用重复（`repeated_tool_call`）
- 用户中断/审批拒绝
- 模型错误、认证/配额不可恢复错误

### 2.3 上下文管理

- 每次调用前按 UTF-8 字节保守上界计算 messages + tools 预算。
- 超预算时删除最早的工具调用组，再截断旧 observation；不会删掉 system/user 初始问题。
- 工具结果有 `max_output_chars` 上限。

## 3. 工具面

`src/meetingvar/agent/meeting_tools.py` 注册的工具全部是只读：

| 工具 | 作用 |
|---|---|
| `search_sessions` | 跨会话关键词检索，返回 session/turn/原文片段、`definition_turns` 候选 |
| `list_sessions` | 最近会话、句数、片段数 |
| `get_transcript_window` | 按 turn id 读原始发言，引用核对的唯一来源 |
| `search_memory` | 项目事实记忆检索（FTS5/BM25，支持类型/状态过滤，返回 score/why） |
| `read_fact` | 读完整事实（含状态、版本、来源） |
| `search_conversations` | 跨对话检索标题、滚动摘要与消息片段 |
| `read_conversation` | 读取某个历史对话的摘要与最近消息 |
| `search_project_knowledge` | 项目文档 + 会议转写的 RAG 混合检索（FTS5 + BGE） |
| `read_project_chunk` | 读取 RAG 片段原文与来源 |
| `compare_sessions` | 同一查询下对比两个会议 |
| `get_session_overview` | 会话概览、说话人、开头几轮 |

写入仍走受控流程：会后 `PostMeetingAgent`、聊天“记住…”动作卡、会后快速候选，全部需要用户确认；模型没有自由写记忆的工具。新增写工具必须使用 `ApprovalBroker` 的一次性参数绑定 token。

## 4. 身份、作用域与审批

`src/meetingvar/agent/policy.py`：

- `Principal(actor_id, roles, project_scopes)`：每次工具调用都携带应用身份。
- `enforce_tool_policy`：
  - 项目路径必须在 principal 的 project scope 内；
  - 写工具需要 `agent:write`；
  - 外部工具需要 `agent:external`；
  - `required_role` 可做更细粒度限制。
- `ApprovalBroker`：
  - 一次性 token；
  - 绑定 tool、actor、project scope、参数 SHA-256；
  - 过期、复用、参数变化都 fail closed。
- 桌面默认 principal 只有 `agent:read` 且 scope 为当前项目；Agent 深度检索因此不可能写文件。

## 5. 可观测性

`TraceRecorder.record_llm()` 记录：

- model
- ok / error
- latency_ms
- prompt_tokens / completion_tokens / reasoning_tokens
- finish_reason
- tool call 名称
- mode（native/json）

token 计数（prompt/completion/reasoning/input/output）保留为数字，便于成本与性能分析；只有密钥类字段脱敏。不记录 prompt、转写、工具结果正文或密钥。文件：

```text
<项目>/meetingvar/agent_memory/trace/agent_run.jsonl
<项目>/meetingvar/agent_memory/trace/agent_run.jsonl.1
```

任务和工具事件仍由 `AgentRuntime`/`ToolRegistry` 记录；状态文件在 `trace/loops/<task_id>.json`。

## 6. 断点续跑

- 每一步后原子写 `trace/loops/<task_id>.json`，包含 question、mode、plan、messages、observations、budget、usage、status。
- `AgentRuntime.resume(task_id)` 恢复任务状态。
- CLI：`meetingvar agent-run --resume <task_id> ...`
- 桥接：`agent_resume`；桌面 UI 暂未暴露续跑按钮。

恢复时保留已完成工具观察，不重复调用；模型从当前对话继续。

## 7. 引用校验

最终答案必须是：

```json
{
  "answer": "...",
  "citations": [
    {"kind": "turn", "session_id": "...", "turn_ids": ["turn_1"], "quote": "..."},
    {"kind": "fact", "name": "..."}
  ],
  "unsupported": [],
  "confidence": 0.0
}
```

校验规则：

- turn 必须是工具实际返回过的 `(session_id, turn_id)`；
- fact 必须是 `search_memory`/`read_fact` 返回过的名称；
- 提供 quote 时，quote 必须出现在对应观察文本中；
- 无有效引用且已有观察时，先纠正一次；仍无依据则 `grounded=false`；
- 没有证据但回答“无法回答”时，`answered=false`，不伪造引用。

## 8. 评测

`src/meetingvar/agent/eval.py` + `meetingvar agent-eval`：

```bash
meetingvar agent-eval \
  --project "D:\项目\数据结构" \
  --cases configs/agent_eval_cases.example.json \
  --max-steps 8 --json
```

指标：

- accuracy（含拒答正确性）
- grounded_rate
- citation_precision / citation_recall
- refusal_accuracy
- avg_steps / avg_tool_calls
- avg_elapsed_ms
- prompt/completion tokens
- stop_reason 分布

示例问题集在 `configs/agent_eval_cases.example.json`。真实运行结果（本地 Qwen3-4B 16K，2026-10-19）：

- 3 题 accuracy 1.0，grounded 0.667，citation precision 1.0，citation recall 0.667，refusal 1.0；
- 平均 4 步 / 2.67 次工具；
- `p 一` 题由 prefetch 找到 turn 242，Agent 读取并正确回答“p 后面加星号”；
- 编码题找到 turn 260，正确回答 p=1、星号=2，括号未明确说明时明确拒答部分结论。

这些是本地小模型结果，不是通用准确率承诺。

## 9. 桌面与 CLI

- CLI：`agent-run`、`agent-eval`、`agent-resume`（桥接）
- 桌面 Agent 页新增“Agent 深度检索”：显示计划、工具步骤、回答、引用状态。
- 桥接：`agent_run` / `agent_resume`，超时 300 秒；使用当前设置的本地/自定义模型。

## 10. 边界

- 外部连接器（邮件/日历/LMS/飞书）、MCP、多 Agent、RBAC 多租户尚未实现。
- 工具在进程内线程执行；超时是 advisory，不是硬沙箱。生产环境应加子进程/容器、文件系统 allowlist 和网络出口控制。
- 桌面本地调用没有服务端身份认证；Principal 是应用内最小权限，不是多用户认证。
- 本地 4B 的检索改写和最终归纳仍可能出错；评测集和引用校验是底线，不是质量保证。
- Agent 深度检索默认只读；写入仍由确定性会后流程 + 用户勾选完成。
- 真实会议/讯飞识别质量/长会稳定性仍未认证。