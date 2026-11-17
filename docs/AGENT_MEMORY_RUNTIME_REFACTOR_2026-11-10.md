# meetingVAR 统一操作与记忆底座改造

日期：2026-11-10
状态更新（2026-11-10）：已完成运行回归、构建并安装0.27.0；665项全量测试和27项真实桥接验收通过，安装版SELFTEST-OK并正常启动。下文保留前一阶段的静态审查记录，不代表当前最终状态。最终验收及边界见 C:\Users\Lenovo\.zcode\workspace\default\meetingvar_release_0.27.0\RELEASE_NOTES.md。未对真实项目执行历史迁移。
仓库：C:\Users\Lenovo\projects\meetingVAR

## 核心契约

执行事实不等于助手回复，历史生成成功不等于文件目前仍存在。

- 每项操作由 project/thread/turn/run/tool-call/operation 身份关联。
- 项目内 `meetingvar/agent_memory/operations.sqlite3` 是新操作回执来源，只有实际执行入口写入。构造和查询空项目不创建库。
- `pending → running` 或 `awaiting_approval → approved → running` 通过事务领取；终态 succeeded/failed/cancelled/interrupted。取消运行请求单列 cancel_requested。
- 参数摘要与原始参数 SHA-256 绑定审批；回执返回完整结构化结果，展示可以截断但不得丢失持久记录。
- 产物登记项目相对路径、生成时绝对路径、大小、mtime、哈希、操作来源；当前存在性与版本另行核验。超过64MiB的文件仅做大小/mtime身份核验，明确标识，不宣称完整哈希验证。
- 聊天JSON是对话与兼容卡片，操作状态从journal投影。旧“已生成”文字不自动迁成成功记录。旧pending提案仅在用户明确点击时以未执行提案接入新库。

## 已写入源码

### 会话、工具循环和执行

新增 `src/meetingvar/agent/operations.py`、`context.py`。

- ChatStore使用递增seq和summary_until_seq；不再按240条数组长度永久冻结摘要。保留完整会话记录，近期模型上下文仍有预算。JSON读改写加线程/进程锁。
- Chat/Agent共享上下文构建；Agent默认走模型工具循环，不再由“文件在哪”“现在生成”等问句正则决定执行状态。删去旧文档位置/大纲跟进/独立项目证据问答快捷路径。
- 注册 query_operations、query_artifacts、prepare_memory、prepare_evidence_pack、prepare_project_index；原文件/命令/会议/图谱工具保留。
- 所有聊天动作通过统一审批claim/result；新增finalize_action接收可信桌面回执。UI-only审批不再直接done。
- ToolRegistry为每次实际调用保存结构化回执；工具提案以tool.*记录，不当作实际写入。
- 规划器和执行模型同时收到共享history；续跑包含最近8条观察；checkpoint保留完整observations、审批动作、身份、压缩和续跑元数据。
- 待审批任务写waiting_approval，恢复不会重放提案；已有动作均终结时可对账收口。未知命令副作用不自动重放。
- SSE index=0按合法索引累积分片；恢复内容delta实时传递，不保存或转发模型私密推理。
- 最终答案不再用一个丢失上下文的二次模型调用改写。

### 桌面和前端

修改 `desktop/app.py`、`desktop/engine_bridge.py`、`desktop/agent-ui/app/page.tsx`、`lib/bridge.ts`，保留视觉样式。

- 聊天run UUID、项目/会话/run事件信封，前端匹配三身份；工具活动按call ID区分。后端拒绝运行中进程被覆盖，取消明确指定运行身份。
- 宿主私有处理器真正起会/停会/理解并提交执行回执；前端运行中审批会继续查状态，而不是把批准当成功。
- 非聊天记录覆盖普通会议起止、文件转写及bridge的人工KG/事实/索引/理解/词表/纪要修改入口。起会成功仅表示进程启动，不等同完整录制或归档成功；停止操作等对应进程退出。
- 文件预览token绑定项目根；异步chat_new、图谱、预览、纪要、找回、转写等路径补epoch防护。
- 自动理解阶段检查ok:false；空manifest不再显示校验通过。
- kg_list不再触发自动合并；事实修改后尝试增量投影，失败返回projection_pending/mutation_applied。

### 知识生命周期

- RAG清理确认缺失的源和成功读取的短/空源；读失败不当删除。检索时拒绝缺失/变更/失去项目归属的源。
- chunk ID绑定内容revision；同内容仅mtime变化时同步快照，避免错误地永久过滤。当前新鲜度快速判据是大小/mtime，不能抵御刻意伪造相同元数据。
- SessionCatalog支持project_root，工具、证据包、找回、GraphRAG按项目成员访问；session_ids_for_project(strict=True)区分损坏/权限异常与正常缺文件。
- GraphStore保留human和deleted tombstone，自动upsert不得覆盖人工修订；reset保留受保护人工端点关系。人工操作追加kg_human_events。
- consolidate_duplicates改为只报候选，不自动语义合并/物理删证据；resolve仅精确active名称/别名，不把FTS首条当同一实体。
- GraphBuilder默认增量构建，先撤回旧自动事实投影，再投影当前有效事实。GraphRAG源缺失/失效时不退回陈旧quote。
- 同名冲突生成独立待审候选，保留旧active事实；跨会议source_refs不混挂，新增带source_id/locator的evidence_refs。旧引用明确为legacy_unversioned。
- 保存/编辑保留有效期，查询不返回未来尚未生效事实；旧指代记忆提案只取用户消息，不将助手文字标成用户授权。

## 验证事实

本轮没有运行项目代码、自动化测试、真实模型或UI。以下仅为静态检查：

1. Python AST解析覆盖改动及新建Python源码，语法错误0。
2. Python symtable检查15个核心文件，没有发现未解析的全局符号候选。此检查不等于类型检查或运行正确。
3. TypeScript createSourceFile解析page.tsx和bridge.ts，parseDiagnostics均为空；未执行tsc类型检查或Next构建。
4. git diff --check通过。工作树原有大量未提交修改；diff总行数包含历史工作，不归为本轮新增。
5. 已有desktop/app.py和desktop/build/setup_app.py的反斜杠字符串SyntaxWarning仍存在，不是本轮新增；未擅自修改构建文件。

新增测试源码（未运行）：

- tests/test_operation_store.py：13项，审批claim、并发领取、终态、结果回读、删除/修改产物、scope、脱敏、取消和中断。
- tests/test_agent_memory_architecture.py：12项，超过240消息、共享上下文、失败与pending、UI-only回执、通用追问路由、Chat模式、45观察checkpoint、工具结果和SSE零索引。
- tests/test_knowledge_lifecycle.py：10项，删源/空源/revision/touch/成员表损坏、精确实体、人工保护、tombstone及跨会议引用。
- 已更新旧chat/loop/memory/evidence/RAG/desktop相关测试契约和项目成员fixture。

## 尚未宣称完成的边界

- 运行回归、Windows录制硬件、流式模型兼容性、双项目切换、UI审批端到端、崩溃恢复仍需实测。
- 旧历史完整迁移/所有原始来源不可变revision、全局SourceRegistry、敏感内容分类与保留/清理策略没有完整实施。正则脱敏不能保证所有自由文本输出无敏感数据。
- 旧图谱已发生的错误合并或丢失证据不会被本轮自动修复；不擅自改真实用户库。
- 人工图谱保护当前为同库持久事件+tombstone，不是独立源库完整事件回放迁移。
- 手动VAR/说话人命名/商业导出删除等全部旁路行为尚未逐一统一登记；普通录制、转写、主要业务修改已接入。
- 命令线程/进程取消不能保证回滚副作用；未知结果保留interrupted，不承诺恰好一次。重启时全局扫描对账与lease/heartbeat仍需完善。
- 长会话全量JSON与SQLite结果现无压缩归档/磁盘配额；大量内容可能带来性能与留存成本。
- 会议材料的事实语义、否定/假设、模型抽取质量仍需人工标注评估，不能用引用存在性代替正确率。

## 用户执行的验证顺序

先在PowerShell只运行独立操作库测试（不启动应用）：

```powershell
& 'D:\miniconda3\envs\meetingvar\python.exe' -m unittest discover -s 'C:\Users\Lenovo\projects\meetingVAR\tests' -p 'test_operation_store.py' -v
```

后续依次验证test_agent_memory_architecture.py、test_knowledge_lifecycle.py和受影响旧套件，再做临时项目UI验收。不要先在真实项目跑迁移，也不要跳过失败就打包。

本轮未改版本、未提交/推送、未替换EXE或安装包；已安装外壳未重装。注意已安装外壳若按配置读取此源码仓作为engine，后续启动engine可能使用这些源码，故静态检查不等于生产已验收。
