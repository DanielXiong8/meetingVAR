# 0.7.0：可审批会议动作与专用技能

日期：2026-10-20。目标：把 0.5.0 仍留在旧页面的写操作/会议控制，变成对话里可确认的动作；同时修复“最近会议总结”被重复工具调用耗尽步数的问题。

## 1. 已修复的真 bug

截图场景：
> 列出最近的会议，并总结每场的主要决定、行动项和未决问题。  
> Agent 8 次工具，`step_budget_exhausted`，其中 `search_sessions` 同参数重复 5 次。

根因：重复调用检测把模型生成的 `call_id` 算进哈希，模型每次换一个 id 就被当成新调用。

修复：
- 重复键只算 **工具名 + 参数**，不算 call_id。
- 第二次相同调用不再执行，返回“change query/strategy”给模型。
- 第三次相同调用直接 `repeated_tool_call` 停止，不再耗到预算上限。
- 新增回归 `test_identical_tool_call_is_not_executed_twice`。

## 2. 最近会议总结技能

通用 ReAct 不适合“固定读最近 N 场会议并逐场总结”。

新增 `MeetingDigestSkill`：

1. 按 mtime 找最近 5 场**有转写**的会话（自动跳过空会话）。
2. 每场优先读薄纪要；否则读首尾 + 关键词命中 turn。
3. 每场一次结构化 LLM 调用，提取 summary/decisions/actions/open_questions，evidence_turns 本地校验。
4. 确定性格式化为“最近会议摘要”，逐场列决定/行动项/未决问题，附 turn 引用。
5. 本地 Qwen3-4B 实测：5 场会议约 **7 秒**，13 条引用，`completed`，不再耗尽步数。

`ChatAgent` 对“总结/纪要/复盘 + 最近/每场/会议”直接路由到该技能。

## 3. 对话内可审批动作

新增工具（只产生待确认动作，不直接写/不直接录音）：

| 工具 | 动作 |
|---|---|
| `prepare_focus` | 生成会前关注候选 |
| `prepare_meeting_facts` | 提取会后五类事实候选 |
| `start_meeting` | 请求开始会议 |
| `stop_meeting` | 请求结束会议 |
| `start_project_reading` | 请求开始全库理解 |
| `get_project_terms` | 读取全库候选词/确认词 |
| `save_project_terms` | 请求合并写入 #!keep 词表 |

流程：

```text
用户：开始会议
  → 模型调用 start_meeting
  → 工具只返回 ui_action，不启动录音
  → 对话里出现“开始会议 · 需要你确认后执行”卡片
  → 用户点确认
  → ChatAgent.execute_action 记录确认，UI 调用真实 start_meeting
```

写操作同理：

- `save_focus` → 用户确认后 `FocusAgent.save_items` 写 `focus.json`；
- `save_meeting_facts` → 用户确认后 `PostMeetingAgent.save_items` 写项目事实；
- `save_terms` → 用户确认后合并现有 `#!keep` 词，写入词表。

动作持久化在会话 JSON 的 `pending_actions`，状态会合并回历史消息；重复确认会被拒绝。

## 4. 管理页改为抽屉

项目、会议库、设置、旧 Agent 工作台不再替换聊天工作区：

- 作为右侧抽屉覆盖在对话上方，宽度最多 1120px；
- 背景保留对话，点击遮罩/关闭按钮回到对话；
- 从侧栏打开，不再“离开会话”。

## 5. 验证

- 完整 462 项测试，0 跳过，462 通过；compile+CLI 仿真通过。
- 新增/覆盖：
  - 重复工具调用停止；
  - digest skill 5 场总结；
  - start_meeting 审批动作只执行一次；
  - save_focus 写入项目记忆；
  - save_terms 合并 #!keep；
  - 会话历史/工具引用。
- 0.6.0 安装版已实测同一动作流程（0.7.0 继承并增加词表动作/抽屉）：
  - 发送“开始会议”出现待确认卡片，点确认后 RTASR 连接、音频有输入、录屏启动；结束会议正常；
  - 发送“列出最近的会议，并总结每场的主要决定、行动项和未决问题。”返回逐场摘要，5 次 `digest_session`，不再 `step_budget_exhausted`。

## 6. 商业软件差距（必须如实）

已经具备：安装/卸载/快捷方式、版本、`--selftest`、本地优先、审计、审批、用量统计、安装包哈希。

仍未具备：

- 代码签名、公证/杀软白名单；
- 自动更新、崩溃上报（需用户同意）、遥测开关；
- License/激活/订阅计费；
- 多用户/RBAC/租户隔离（桌面版目前是本机单用户）；
- 外部连接器/MCP（日历、邮件、LMS、飞书）、企业 SSO；
- 子进程/容器硬沙箱（工具超时仍为 advisory）；
- 真实设备矩阵、讯飞识别质量、长会稳定性验收；
- 隐私政策、服务条款、第三方许可证清单、数据保留/删除 UI；
- 客服/工单/知识库/发布渠道与签名证书。

这些不是“再加一个按钮”，而是商业发布前的独立工程/合规工作。