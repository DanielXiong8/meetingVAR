# meetingVAR 开发资源与分支状态（2026-10-19）

本文用于黑客松结束后的持续开发交接。只记录资源位置、当前状态和已知边界，不包含API密钥、真实会议转写或用户配置内容。

## 1. GitHub 分支拓扑

远端：<https://github.com/DanielXiong8/meetingVAR>

| 分支 | 当前提交 | 状态与建议 |
|---|---|---|
| `main` | `91aa4dc` | 已合并 PR #34（`feat/desktop-ui`），是当前主线基线。 |
| `feat/local-model-audio-fix` | `f822206` | 0.1.3本地GPU/API诊断＋双源音频时间轴＋外部API截断修复；待评审合入。 |
| `feat/agent-runtime` | `c4528d9` | Agent 闭环 + 0.4 深色工作区 + 0.5 对话式 Agent + 0.7 摘要/审批 + 0.8 多模态文件/查看器 + 0.8.1 项目树优先 + 0.8.2 内容优先类型识别 + 0.9.0 干净 Windows 运行时；已打 `v0.2.1`~`v0.15.0` tag。 |
| `integration/28-reconciliation` | `af92813` | 含队友PR #32/#33；相对main有19个main独有提交、2个该分支独有提交，已分叉，合入前需先合并/变基最新main。 |
| `feat/desktop-ui` | `a9abea5` | 已由PR #34合入main，可保留作历史或清理。 |
| `feat/term-curator` | `1e778c7` | 已并入integration，旧分支。 |
| `fix/speaker-diarization` | `77569d8` | 已由PR #32并入integration，旧分支。 |
| `pr-a` / `pr-b` / `pr-c` | 57ed2c1 / 2bdc1a4 / 7549190 | 已完成的历史PR分支。 |
| `codex/b-feedback-loop` | `628e77f` | 10月11日旧实验分支。 |
| `codex/local-acceptance-fixes` | `0a16d53` | 10月13日旧验收分支。 |

**建议顺序：** 先评审并合入 `feat/local-model-audio-fix`；再由队友把 `integration/28-reconciliation` 合并最新main解决分叉；确认无回归后统一旧分支的清理。删除远端分支会改变协作者环境，本文不自动执行。

新建PR入口：<https://github.com/DanielXiong8/meetingVAR/pull/new/feat/agent-runtime>

## 2. 本机代码与运行环境

| 资源 | 位置 | 当前状态 |
|---|---|---|
| 工作仓库 | `C:\Users\Lenovo\projects\meetingVAR` | 分支 `feat/agent-runtime`，版本0.15.0。 |
| 引擎环境 | `D:\miniconda3\envs\meetingvar` | Python 3.10，editable安装指向工作仓库；修改引擎代码后下次启动生效。约6.8GB。 |
| 桌面安装 | `C:\Users\Lenovo\AppData\Local\Programs\meetingVAR` | 已安装0.15.0；注册表版本、快捷方式与 `--selftest` 已核验。 |
| 配置与日志 | `C:\Users\Lenovo\AppData\Roaming\meetingVAR` | `ui_config.json`、UI/模型日志及旧配置备份；含本机路径，不入库。 |
| 本地模型根目录 | `D:\env\models\models` | 约11.8GB，含Qwen3-4B GGUF、Qwen3-4B AWQ/FP8、SenseVoice、Paraformer、CAM++等。 |
| 原生推理后端 | `D:\env\llama.cpp\b11669-vulkan-x64` | 官方Vulkan版llama-server，支持本机RTX 5060；不随源码或UI安装包分发。 |

## 3. 构建、安装包与提交材料

| 资源 | 位置 | 说明 |
|---|---|---|
| 0.1.1/0.1.2/0.1.3发布目录 | `C:\Users\Lenovo\.zcode\workspace\default\meetingvar_release_0.1.*` | 历史版，各约65MB。 |
| 0.2.x~0.15.0发布目录 | `meetingvar_release_0.2.*` ~ `meetingvar_release_0.15.0` | 0.15.0 为当前版，含干净 Windows 文件解析、多模态查看器、项目树、对话式 UI、摘要技能与可审批动作；旧版保留。 |
| 仓库构建产物 | `meetingVAR/dist`、`dist_installer`、`build` | 0.15.0 的 EXE 与安装包在 `dist`/`dist_installer`；旧目录内仍有早期产物，使用前看时间与 SHA256。 |
| 黑客松提交材料 | `C:\Users\Lenovo\OneDrive\Desktop\EE黑客松_20261018` | 含源码包、README、自查声明、课堂案例核验、历史备份。 |
| 最新提交ZIP | `...\校园学习生活_meetingVAR_提交包_20261018_音频修复版.zip` | 已含音频时间轴修复；ZIP校验通过。 |
| 旧最终版ZIP | `...\校园学习生活_meetingVAR_提交包_20261018_最终版.zip` | 修复前版本，保留对照，不应作为最新提交。 |

## 4. 本机会议证据与实验数据

以下数据位于仓库 `data/`，已被git忽略，**不得作为源码提交，也不应在未核对前删除**：

| 目录 | 规模 | 内容 |
|---|---|---|
| `data/clips` | 约6.76GB，166个目录，约46500个文件 | 已保存片段的WAV/屏幕帧/合成视频。 |
| `data/pool` | 22场会话，165个片段记录 | 会话元数据与转写；可能包含真实会议内容。 |
| `data/verification` | 约1.2GB，5次运行 | 自动化测试产物，可按时间归档；删除前确认无待查日志。 |
| `data/reports` | 3个会话 | 纪要归档。 |

课堂实验产物在工作区 `C:\Users\Lenovo\.zcode\workspace\default`：`test3_judge_argmax_2130-2330.mp4`（约343MB）、转写、裁判JSONL及报告。原始课堂媒体不随源码包分发。

## 5. 验证快照（2026-10-21 晚，0.10.3）

- 完整Python3.10依赖环境：507项测试、**0跳过、507通过**（67.81秒），`compileall`通过，完整CLI仿真通过。
- 最小依赖CI模拟：507项发现，507项通过，编译通过（当前引擎环境已含 pymupdf/pypdf/imageio-ffmpeg）。
- 提交包内音频专项：24项通过，含90分钟虚拟双源到RTASR发送链路。
- Agent Runtime v2：模型原生 function calling、有界 ReAct/Plan-Execute（最多 8 步）、Principal/项目作用域、审批 token、步数/工具/token/时间预算、引用校验、LLM 级 Trace、断点续跑、黄金问题集评测。CLI `agent-run`/`agent-eval`，桌面 Agent 页新增“Agent 深度检索”。
- Agent专项：37项通过（工具审批/超时/幂等、Trace脱敏、检查点、事实记忆、五类事实核对/审批/dry-run、记忆问答、会前关注清单、CLI）。
- 0.2.1真实窗口复测：本地模型生成并保存关注清单、19个有转写会话过滤、0事实问答、会后0候选与模型停机错误提示、设置页本地/自定义厂商验证、短会开始/结束（RTASR连接、8句字幕、录屏缓冲约7.8MB、零命中丢弃）均通过。详见 `docs/BUGFIX_0.2.1.md`。
- 0.2.1安装包：`meetingVAR-setup-0.2.1.exe` SHA256 `467eb81aecf04aab5c828c148967eec8ff71c0d816510f2fa019d3c0a74f8cde`；内置 `meetingVAR.exe` SHA256 `3cc0f8512178d9b6310df2c5b93bf7667315bc04162ca6a5cabdf668e4fc5950`；安装后 `--selftest` 通过。
- 0.2.2全库理解冷启动：清空项目 `meetingvar/understand` 与 `interest_context.json` 后，真实 UI 对 `D:\项目\数据结构` 读完 **14/14 块、60156/60156 字、100%**，写入 `interest_context.json`；其中密集合块自动降级为精简关键词模式。详见 `docs/BUGFIX_0.2.2.md`。
- 会议证据基准 v1（2026-10-21）：新增事件级 `evidence_benchmark`（labels/predictions schema、recall、clip precision、每小时误保存分钟、关注点覆盖、引用支持率、20场/40事件/80%召回/≤10分钟误保存发布门禁）；test2 真实会议首次基线（机器预标注，未核验）= 4/6 事件召回 66.7%、漏 evt_005 KG定位大讨论与 evt_006 agent-KG负责人、误保存 3.05 分钟/小时、保存 6.48 分钟/小时。工具 `scripts/meeting_benchmark.py`，方法见 `docs/MEETING_EVIDENCE_BENCHMARK.md`。
- Test5 12 条考试关注问题 L1/L2/L3 对比（2026-10-21）：同一批 12 个 10 分钟课堂片段、同一份本地转写；L1 热词命中 8/12（66.7%）、L2 关注文档 9/12（75.0%）、L3 本地 4B 裁判 4/12（33.3%）。L1/L2 易把“提到相关词”当命中；L3 受 ASR 数学术语错误影响偏保守。**修正：新增 `meetingvar.lecture.find_focus`，把 10 分钟转写切成 90–120 秒窗口，关键词召回 + 小窗 LLM 裁判，返回带时间戳/证据 turn 的片段；同一批 12 个转写命中 9/12（75%），F1/F5 抽样段本身无该内容、F10 无对应 ODE 课次。** 另修复 8GB GPU 自动选 CUDA 后模型加载/推理 OOM：auto 要求总显存≥10GB，推理 OOM 也回退 CPU。
- Test5 考试题驱动关注问题 pilot（2026-10-21）：从 2021–2025 期末卷提炼 12 条关注问题（`benchmarks/meeting_evidence/test5_focus_questions.json`）；首轮 4 个 case（F2 链式法则/F4 极值/F6 第一型曲面积分/F9 级数）各抽 600 秒、用关注文档跑 `replay`：**4/4 命中主题**，11 个片段中 9 个关键词相关（81.8%），保存 2,355 秒≈58.9 分钟/小时；F6 的 2 个低分片段经人工查看仍在讲参数化，说明关键词 precision 是下限。结论：能命中主题，但 300 秒回溯窗不精确、样本非盲测。见 `docs/TEST5_EXAM_FOCUS_PILOT.md`。
- 0.15.0 Agnes API 接入（2026-10-25）：实测 `C:\Users\Lenovo\OneDrive\Desktop\keys\agnes ai\.env` 的 key 可调用 `https://apihub.agnes-ai.com/v1` 的 `agnes-3.0-flash` 和 `agnes-2.5-flash`（HTTP 200）。3.0 是推理模型，默认 4k 会 reasoning 截断；`reasoning_effort=none` 后合法 JSON，但约 51–93 秒/块；2.5 + none 约 4.7–6.6 秒，KG 抽取 6 实体/5 关系，明显优于本地 Qwen3-4B。新增 `MEETINGVAR_LLM_REASONING_EFFORT`、设置页推理强度、Agnes 3.0/2.5 预设和“保存模型设置”按钮；`llm.py` 截断重试不再因 thinking=disabled 直接放弃。完整 533 项测试通过（72.78 秒）。详见 `docs/AGNES_API_0.15.0.md`；API Key 不写源码/仓库。
- 0.14.0 知识图谱 Phase 1（2026-10-25）：新增 `kg/extract.py`（严格 JSON 实体/关系抽取、evidence turn 校验、surface 必须在原句出现、`RELATION_ALLOWED` domain/range 双层校验）、`kg/link.py`（精确/别名 → BGE 余弦 → 可选 LLM 灰区 → provisional 新节点）、`kg/ingest.py`（会议 turn 分块、episode hash 幂等、关系/实体增量入图、合成“本次会议/本项目”端点）。CLI `kg-ingest --session/--all/--force`；会后会议事实保存后 best-effort 重建规则图，LLM 深度抽取显式运行。本地 Qwen3-4B 实测：加 `/no_think` 后 2 句话约 5–7 秒，4–5 实体/1 条有效关系；4B 关系抽取质量有限，生产建议更强模型。完整 533 项测试通过（74.15 秒）。详见 `docs/KNOWLEDGE_GRAPH_PHASE1.md`。
- 0.13.0 知识图谱 Phase 0（2026-10-25）：新增 `src/meetingvar/kg/`：SQLite 属性图（`kg_nodes/kg_edges/kg_episodes/kg_aliases/kg_nodes_fts`，双时间 `valid_from/valid_to`、`observed_at`、证据 `turn_ids/quote`）；`GraphBuilder` 不调 LLM，仅用现有 FactStore + 会议/说话人规则构图（Project/Meeting/Person/Decision/Action/Commitment/Risk/OpenQuestion/Topic；part_of/derived_from/decided_in/assigned_to/owned_by/due_on/speaks_in）。GraphStore 提供 `find_nodes`/`resolve`/`neighbors`/`timeline`/`path`/`stats`；CLI `kg-build/kg-find/kg-neighbors/kg-timeline/kg-path/kg-stats`；Agent 只读工具 `kg_find_entities/kg_neighbors/kg_timeline/kg_path/kg_stats`。测试项目实测 8 节点/13 边，路径/时间线/过期边过滤/重建幂等通过。完整 530 项测试通过（79.84 秒）。详见 `docs/KNOWLEDGE_GRAPH_PHASE0.md`；后续 Phase1=LLM实体关系抽取+实体链接。
- 0.12.0 向量记忆与项目 RAG（2026-10-23）：新增本地 `BAAI/bge-small-zh-v1.5`（CPU、512维、模型缺失自动降级）；FactStore 改为 FTS5/BM25 + BGE 余弦混合检索，语义-only 命中有 0.48 门槛；40题基准 recall@5 1.000、top1/citation precision@1 0.852、负对照 1.000、过期默认视图 1.000。新增 `agent/rerank.py`（LLM listwise 重排，失败退回 BGE 余弦）；项目 RAG `rag.py` 索引项目文档+已保存会议转写（约850字分块、增量 hash、FTS5+向量、chunk 时间戳/turn_ids），Agent 工具 `search_project_knowledge`/`read_project_chunk` 和 document 引用校验；CLI `rag-index`/`rag-ask`；Agent 页新增“项目知识库 RAG”卡（建立/强制重建/状态/提问/引用）。对话摘要改为优先 LLM 语义摘要（失败退回抽取式）；新增 LLM 冲突裁判 `memory_judge.py` 接入聊天/会后写入。记忆 UI 增加“彻底删除”（文件+版本快照）。30天纵向模拟：recall@5 1.000、冲突识别 True、过期默认视图干净、重载持久化 True；真人30天评测提供 `scripts/memory_human_eval.py` 标注/计分工具（尚未伪造真人结果）。完整 525 项测试通过（67.20 秒）。详见 `docs/RAG_AND_EMBEDDING_0.12.0.md`、`docs/MEMORY_HUMAN_EVAL.md`。
- 0.11.0 商业级项目记忆（2026-10-22）：Markdown 事实源 + SQLite FTS5 可重建索引；中文 bigram/BM25 排序，返回 score/why；业务同义词与日期约束；统一主聊天 `search_memory` 与 `MemoryQAAgent` 召回。写入侧新增聊天“记住…”审批卡、会后无模型快速事实候选（停止会议自动弹出）、同名版本快照/同 canonical 合并；治理侧新增 active/superseded/expired/pending-conflict/rejected/deleted 生命周期、`valid_from/valid_to`、置顶、软删除、编辑、冲突三选一、`MEMORY.md` 冲突区。对话记忆新增 12 条消息滚动摘要与 `search_conversations`/`read_conversation` 工具，引用校验支持 `kind=conversation`。Agent 页记忆卡支持状态筛选、编辑、置顶/取消、删除、冲突处理。40 题合成基准：recall@5 **1.000**、top1/citation precision@1 **0.852**、负对照 **1.000**、过期默认视图干净 **1.000**、冲突识别/不进入默认检索/旧事实仍生效均 True、命中原因覆盖 0.825。完整 519 项测试通过（68.32 秒）。详见 `docs/MEMORY_V2_0.11.0.md`。
- 0.10.7 ASR 对比与语义定位（2026-10-22）：同一堂 52.6 分钟课堂（3155 秒）整段对比 SeACo 与讯飞 LFASR。SeACo 1149 句/14,087 字/RTF 0.149/术语 85 次 11 种/4 秒窗重复率 7.0%；LFASR 无热词 865 句/14,140 字/RTF 0.061/术语 70 次 5 种/重复率 0%；LFASR 加术语热词 866 句/14,148 字/RTF 0.057/术语 75 次 5 种。热词分隔符修正为竖线 `|`（空格会触发讯飞 100016）。跨引擎字符 CER 0.135（只是分歧率，不是人工 CER）。逐段核对发现 SeACo 把“一元函数”听成“隐函数”，因此术语次数不能单独当准确率。语义定位修复四个真问题：裁判 prompt 现在给每行 `[turn_id]`（此前 4B 编造 turn_1，全部被校验丢弃）、候选按术语出现次数加权、合并重叠窗口按 turn_id 去重、片段起点对齐裁判证据。LFASR 整堂转写 + 本地 Qwen3-4B 裁判：F1 方向导数/梯度命中 05:01 定义、10:00、20:11；F2 链式法则命中 34:57、39:14、42:58；F3 隐函数定理负对照 0 命中。Whisper large-v3 CPU 3 分钟：无 VAD 325.5 秒/118 段/首段“组合”幻觉；VAD+关上文条件+数学 prompt 后 181.9 秒/95 段/14 次术语/无幻觉，未接产品。音频前端 `--denoise`（高通+afftdn+响度）实测 3 分钟术语命中 12→11，故保持可选、不作默认；`highpass+loudnorm` 为 12，与原始持平。并加测 2026-02-27 的 2h34（9271 秒）整堂：SeACo 3079 句/38,663 字/RTF 0.171/重复 9.3%/术语 318 次 28 种；LFASR+热词 2907 句/38,876 字/RTF 0.082/重复 0.3%/术语 265 次 15 种；跨引擎 CER 0.126。2h 转写定位“R^n 拓扑与开集闭集”命中 04:32/08:10/22:03/138:02 四段，“多元极限 ε-δ”命中 56:00/124:55 两段；同时修正“两宽词共现即覆盖裁判 false”的误报规则，67:53 只提方法未讲定义的窗口不再强行保留。**没有人工逐字稿，仍未报 CER/WER**。完整 511 项测试通过。详见 `docs/ASR_AND_FOCUS_0.10.7.md`。
- 0.15.0安装包：`meetingVAR-setup-0.15.0.exe` SHA256 `6634724dd76e5ccf6cf6a7915379bfbc7ca180c5915c53ff6a7cf223c3d2c881`；内置 `meetingVAR.exe` SHA256 `89a06cc3a8b822096e4f14a9d584011d89248db59672182233b147745f6c41c5`；静默安装与 `--selftest` 通过（注册表版本 0.15.0）。本机已把 `%APPDATA%\meetingVAR\ui_config.json` 配置为 Agnes 2.5 Flash + reasoning_effort=none（key 仅存本机配置，不写仓库）。
- 0.14.0安装包：`meetingVAR-setup-0.14.0.exe` SHA256 `7000d13c9f36fed01ef2fd67fd5c643785185d08f5cff2af7a9ccdd795df83a8`；内置 `meetingVAR.exe` SHA256 `8272dfe08f4f0e2fb4b027d1b7d0fce466295579fb215e2d315e6572c8a55bb3`；静默安装与 `--selftest` 通过（注册表版本 0.14.0）。
- 0.13.0安装包：`meetingVAR-setup-0.13.0.exe` SHA256 `14ae9877adaaca1af591c52086da6220bb77719d3ee32a71a20d2e65b19f01fe`；内置 `meetingVAR.exe` SHA256 `d36c9e785fa23fde75587263554e893ac09e5832dbda2e68cd83bbe6c43ba7d8`；静默安装与 `--selftest` 通过（注册表版本 0.13.0）。
- 0.12.0安装包：`meetingVAR-setup-0.12.0.exe` SHA256 `4271909747ce26280151c72b10ee3e1c7bc18031c0ef27e70d25db2a08902d7f`；内置 `meetingVAR.exe` SHA256 `46dab08ee8e205f9bdbb8355d45c004aa16bd1ce1e8a1243551bd6269dc590bd`；静默安装与 `--selftest` 通过（注册表版本 0.12.0）。
- 0.11.0安装包：`meetingVAR-setup-0.11.0.exe` SHA256 `048e03ba99d6966fa5b56ed4754c390355e6f3c51d065951abd5406de792ebef`；内置 `meetingVAR.exe` SHA256 `868cfc5c5a5302428a06d3f84da88dccb96a63459567c90f97ab365e919f9d5e`；静默安装与 `--selftest` 通过（注册表版本 0.11.0）。
- 0.10.7安装包：`meetingVAR-setup-0.10.7.exe` SHA256 `2123331a125dc349e796c1fa08fdece57a3c982ce280c4cdb91e6b915190af0d`；内置 `meetingVAR.exe` SHA256 `ace9a933494553e94687381c5c0b85ac68b42ffa6e5a2b7717d4452374789a52`；静默安装与 `--selftest` 通过（注册表版本 0.10.7）。
- 0.10.5 离线整段转写：`transcribe --lecture/--offline` 走完整音频一次解码（SeACo + 术语 hotwords），不再 8 秒切块。3 分钟课堂实测：chunked 122 句/10 次“方向导数”/明显重复碎片 → offline 74 句/9 次、句子完整（“线性代数告诉我们说……形成一个 RN 的基底”），无自动片段。术语表+hotword+重复字修复保留。完整 507 项测试通过（67.81 秒）；0.10.5 `--selftest` 通过。
- 0.10.5安装包：`meetingVAR-setup-0.10.5.exe` SHA256 `94504425aad7632e4a6d8317420bc61ff888041013c91d52f98eee95edb27a0f`；内置 `meetingVAR.exe` SHA256 `ce8bf8a1c80b6e282a267782de2e2004207fe6d7ec6f1b705a8be107aaf126af`。
- 0.10.4 ASR 专业术语：新增 `configs/lecture_math_terms.txt`（100 个高微术语）；ASR 支持 hotword（SeACo Paraformer 生效，SenseVoice 自动回退）；`transcribe/replay --lecture` 预设 SeACo + 术语表；纠错新增 `zh_dup_char`（方向导导数→方向导数）。3 分钟课堂实测：`方向导数` 从 7 次提升到 10 次，`方向倒数` 从 2 次降到 0；方向导数+偏导数总命中 15→20。完整 507 项测试通过（62.46 秒）；0.10.4 `--selftest` 通过。
- 0.10.4安装包：`meetingVAR-setup-0.10.4.exe` SHA256 `e89184ee7a4ed2d0ad338dc0b182766f6af45a7f2335f58c01645d37ad0d60bd`；内置 `meetingVAR.exe` SHA256 `1dabd6a6651a04a24e867ab911d64ebfcd5a464eba4af680f8c1996c1e27cab8`。
- 0.10.3 课堂回放 T5：34 个 MP4/68.66 小时全部可解码且有音频；3 段 3 分钟 ASR 抽样全部完成，CPU RTF 0.176–0.186，转写时间戳单调，高相似重复率 0–1.6%，`transcribe` 片段数 0。修复：8GB GPU 自动设备 OOM（free<6.5GB 走 CPU，显式 cuda OOM 也回退）；`transcribe` 默认关闭热词/兴趣触发，只写全文；`go/record` 增加按层 60 秒片段冷却；重叠 ASR 窗口增加模糊去重（4 秒窗、长句相似度/包含/bigram），10 分钟样本 442→372 行。数学术语命中仍低（2/0/0），见 `docs/TEST5_LECTURE_REPLAY.md`。
- 0.10.3安装包：`meetingVAR-setup-0.10.3.exe` SHA256 `78b597034b0a177580ae518da31e65204baeed83331108d7f8c9b485f4ac14f3`；内置 `meetingVAR.exe` SHA256 `af245db0673ab2e3dddc2f94322ccf24acd74d537b3be6a4e840067c65c9aaff`。
- 0.10.2 主对话去固定步数：`ChatAgent` 默认 `max_steps=0`、`max_tool_calls=0`（不设固定上限），只保留无进展、重复调用、权限等待、用户取消、时间/上下文上限等语义停止；`AgentBudget` 对 0 视为不设限。到达控制上限时先自动 checkpoint 续跑一次：压缩旧 observations 为摘要、保留原始证据用于引用校验、重置分段预算并继续同一任务；仍无法完成时用 `_best_effort_final()` 基于已查证据生成尽力答案并标注“尚未完整核对”，不再输出“已达到本轮 Agent 预算”。聊天元信息显示“自动续跑 N 次”。新增无限步数、自动续跑/观察压缩、尽力答案三项回归；完整 497 项测试通过（72.26 秒），0 跳过；0.10.2 `--selftest` 通过。
- 0.10.2安装包：`meetingVAR-setup-0.10.2.exe` SHA256 `6bd0a97bd7119dcfe375617c07561ce7f4ae85c3acdbd6c8431214f2af58adfc`；内置 `meetingVAR.exe` SHA256 `d8026553fc937a86dfea17479eb2d5681f463b645326b958cbdfa3690953ea60`。
- 0.10.1 Agent 审批动作闭环：工具返回 `ui_action`（如 prepare_focus 生成 7 条会前关注）后立即以 `action_pending` 完成本轮，回答“已准备…请确认”，不再继续重复调用 project_status/search_memory/prepare_focus 直至 `repeated_tool_call`/预算耗尽；ui_action 按类型+payload 去重；原生 tool calling 与 JSON 兜底两条路径都生效。新增回归测试断言一次工具调用、一个 action、`stop_reason=action_pending`。完整 493 项测试通过（69.72 秒），0 跳过；安装后 `--selftest` 通过（0.10.1）。
- 0.10.1安装包：`meetingVAR-setup-0.10.1.exe` SHA256 `9425732ab195b710b0b01df83edc4fe8f57ea13cbc6f54204d3dfd71fce462ce`；内置 `meetingVAR.exe` SHA256 `cb2af7eb1a9e45b8b2b32e795a9bfb06899fa83daeaaeda5e0e36a56b40e5ed8`。
- 0.10.0 商业与合规层：离线 Ed25519 许可证（签发/验签/机器绑定/到期/试用）、商业与合规设置页（激活/移除/状态）；HTTPS 更新清单 + SHA256 下载校验 + 安装启动；本机脱敏崩溃报告与默认关闭的遥测上传；项目数据分类统计/ZIP 导出/`DELETE` 确认删除；隐私说明、服务条款、第三方许可证与商业就绪矩阵；移除 AGPL PyMuPDF，PDF 改 pdfminer.six（MIT）/pypdf；代码签名钩子 `desktop/build/sign.ps1`。详见 `docs/COMMERCIAL_READINESS.md`。
- 0.10.0安装包：`meetingVAR-setup-0.10.0.exe` SHA256 `900fc49824bad885bd2fe3605b24fe9d62687662752e2c6990398501566fff5d`；内置 `meetingVAR.exe` SHA256 `b68cb1f82b9448a8c1e469071317ad541aa4520fdee37947710305b5ae03cb0c`；安装后 `--selftest` 通过。
- 0.9.0 干净 Windows 运行时：旧版 `.doc/.ppt/.xls` 改为纯 Python CFB/CLX、TextAtom、BIFF SST 解析（`memory/legacy_office.py`），不要求 LibreOffice；PDF 改为 PyMuPDF → `pypdf` → 标准库 Flate 文本流兜底，不再调用 `pdftotext`；ffmpeg 改为 `imageio-ffmpeg` 轮子/`runtime/bin/ffmpeg.exe` 优先，系统 PATH 仅最后兜底；ASR 默认 `auto` 设备，无 CUDA 自动 CPU；桌面壳支持 `runtime/python`、`runtime/repo`、`runtime/models`、`runtime/llama.cpp` 自包含布局；项目文件树与查看器改用 UI 壳内置的 `meetingvar.memory.preview`，没有引擎 Python 也能扫描/预览（实测 engine_python 置空时 5 份文档、DOCX 提取 285 字）；缺少 WebView2 时给出明确安装提示。专项新增 9 项干净运行时契约（旧 Office 无 LibreOffice、PDF 标准库、ffmpeg 轮子、ASR 自动 CPU）。完整 492 项测试通过（70.72 秒），0 跳过；安装后 `--selftest` 通过（0.9.0）。详见 `docs/PORTABLE_RUNTIME.md`。
- 0.9.0安装包：`meetingVAR-setup-0.9.0.exe` SHA256 `e7f34e76d6964e78675a26f44f5f1e53d36389f2b7f3cb194012efba2d2619a6`；内置 `meetingVAR.exe` SHA256 `4ebe8c4ced50c44d7c3dfe3ce69343506c5cbee524e917d6cf3fb26d7fdc87f7`。
- 0.8.2 内容优先类型识别：`detect_file_type()` 先读文件头/容器成员再判类型；ZIP 里区分 DOCX/PPTX/XLSX/普通 ZIP，PDF、图片、音视频、OLE 旧版 Office 与文本均有魔数/内容判断。真实文件 `相关体育活动链接.txt` 实际是 DOCX（OOXML 容器），安装版文件树显示橙色 `DOCX` 标记，查看器标题为“Word 文档（后缀 .txt 与内容不符）”，并提取出 4 条活动链接；普通 ZIP 改名 `.txt` 则显示 `ZIP` 标记与条目标清单，不会当文本读。完整 474 项测试通过（63.14 秒），0 跳过；安装后 `--selftest` 通过（0.8.2）。详见 `docs/FILE_TYPE_SNIFFING_0.8.2.md`。
- 0.8.2安装包：`meetingVAR-setup-0.8.2.exe` SHA256 `a80f2482a4e49fe009a998aa2c9ede2d1cc456508a4aab6e8aeb22f771539199`；内置 `meetingVAR.exe` SHA256 `56d5ba16d845554c22b792d563ce322c56996c56cacc176acedd84d3e4eae3d0`。
- 0.8.1 安装版实测：项目抽屉在窄窗口先把“项目文件”树放在最上方，点击图片显示内嵌图片、点击 DOCX 显示带行号的提取文本与“用系统应用打开”；扫描/刷新项目不再把页标题改成“新任务”，也不再抢走隐藏 Composer 的焦点。完整 469 项测试通过（66.96 秒），`compileall` 与 CLI 仿真通过；安装后 `--selftest` 通过（0.8.1）。详见 `docs/FILE_VIEWER_UX_0.8.1.md`。
- 0.8.1安装包：`meetingVAR-setup-0.8.1.exe` SHA256 `0c5d5e79cc1a0a4c3f911862bddc7b0b504d5f304b26eef96bd71b7bf919014b`；内置 `meetingVAR.exe` SHA256 `9032532733d866241874a5dd573247ecfb7b9c579141440f1d5dbc8b04ed0866`。
- 0.8.0 多模态文件读取/查看器：PDF（PyMuPDF/pdftotext）、DOCX、PPTX、PPT/DOC（LibreOffice）、XML/TSV/代码等统一文本抽取；项目树点击文件打开右侧查看器（文本行号、图片、PDF/Office 提取文本、音视频、系统打开）；路径越界与符号链接拒绝。实测 PDF 24,938 字、DOCX 21,996 字、PPTX 幻灯片+备注、834 个可读文件扫描约 33 秒。详见 `docs/FILE_MODALITIES_0.8.0.md`。
- 0.8.0安装包：`meetingVAR-setup-0.8.0.exe` SHA256 `ef5238ac4bc827541aa0cd0c6af719bc300253b5ca074e20a0c65928e27b0aab`；内置 `meetingVAR.exe` SHA256 `9a402a7736dba70c43f36fc5b6556629df330222bafa8f4ab6cce6ebadef4092`；安装后 `--selftest` 通过。
- 0.7.0 会议技能/审批动作：重复工具调用按名称+参数检测（修复同参数 search_sessions 连跑5次耗尽预算）；最近会议总结走专用 digest skill（5场约7秒、13引用）；会前关注/会后事实/会议起停/全库理解/词表写入变为对话内待确认动作卡；项目/会议库/设置改为覆盖在对话上的抽屉。详见 `docs/CHAT_ACTIONS_0.7.0.md`。
- 0.7.0安装包：`meetingVAR-setup-0.7.0.exe` SHA256 `e81597d5b2e7e3d1c918db9ea23dbe392285a8b08ba016731f9a50de1ec090eb`；内置 `meetingVAR.exe` SHA256 `1b4e297d36796f7e1376405b091dfc6446f0ef368b6c35ed8f1b6d6652264c0a`；安装后 `--selftest` 通过。
- 0.5.0 对话式 UI：主页为真实会话线程（用户/助手气泡、工具轨迹、引用、会话持久化、侧栏最近对话）；Composer 不再跳转旧 Agent 页；会议纪要/项目进展/技能直接发消息；聊天支持 greetings 本地回复、会议问题有界 ReAct、普通问题直答。安装版实测“p 一在课堂里被记作什么？”返回“p 后面加星号”并引用 `t3_base_B:turn_242`，引用已校验。详见 `docs/CHAT_UI_0.5.0.md`。
- 0.5.0安装包：`meetingVAR-setup-0.5.0.exe` SHA256 `f2a0cd1a562cc0c84a2fcf94a8351e214bd952d5aaaca3ca5250347aaca64af9`；内置 `meetingVAR.exe` SHA256 `6cb9926c495c164c5c447bedb70ec7019bb9e44e1a522f8a39cffb20347785c6`；安装后 `--selftest` 通过。
- 0.4.0 UI 重构：ZCode 式深色工作区（左侧项目/会议导航 + 中央 Composer + Agent/会议库/项目/设置分区 + 真实 Token 使用统计）；安装版 0.4.0 已验证主页、项目、Agent、设置/使用统计切换与 1050×680 DIP 窗口尺寸。详见 `docs/UI_REDESIGN_0.4.0.md`。
- 0.4.0安装包：`meetingVAR-setup-0.4.0.exe` SHA256 `bb52d06eb02a90dad39ee7fbce8edc56d374bedd712fc7cdfa8f324371e15e08`；内置 `meetingVAR.exe` SHA256 `1f51befb9a3799cbc4928f537a1e9217a47311f42e049cb7694572c5cc3f5771`；安装后 `--selftest` 通过。
- 0.3.0真实 UI：Agent 深度检索 4 步/3 次工具，回答“p 一被记作 p + 星号”，引用 `t3_base_A:turn_242`，引用已校验；本地模型原生 tool calling 生效。
- 0.3.0黄金集（本地 Qwen3-4B 16K，3 题）：accuracy 1.0、grounded 0.667、citation precision 1.0、citation recall 0.667、refusal 1.0、平均 4 步/2.67 次工具。示例 `configs/agent_eval_cases.example.json`。
- 0.3.0安装包：`meetingVAR-setup-0.3.0.exe` SHA256 `4ac2d9372200f158d4cc634907f2d66aabcf0d07172d884cfa54c114965a494c`；内置 `meetingVAR.exe` SHA256 `de78a7eba0309f250d0498c2560378bf3fa96cd67db06fda2689f83712b12292`；安装后 `--selftest` 通过。
- 0.2.2安装包：`meetingVAR-setup-0.2.2.exe` SHA256 `7a9cc579ea419414158fa1dbe5e02f4dec097138c189481781c1369e232edea4`；内置 `meetingVAR.exe` SHA256 `10871852b183af4574a96cf885e2a2e30bc3134079f800886bff4c9f0b23dd46`；安装后 `--selftest` 通过。
- 外部API输出修复：合成80实体块有效；真实API 5/5合成项目块完成、覆盖100%（约12.6秒）；思考关闭后不再被推理 token 截断。详见 `docs/API_OUTPUT_BUDGET_FIX.md`。
- 以上均为合成/仿真或现有单元测试；真实麦克风、讯飞实际识别质量与长会硬件稳定性仍未验收。

## 6. 当前已知边界

- 音频时间轴修复已通过90分钟模拟双源到RTASR的逐字节验证；真实麦克风、讯飞实际识别质量和长会硬件稳定性仍待现场验证。
- 兴趣判断依赖自定义API；API失败不会退回热词保存，手动VAR仍可用。
- 本地模型0.1.3已验证Vulkan GPU启动与JSON生成；其他机器需按显存调整上下文、设备编号和层数。
- 项目根目录仍无项目级LICENSE；第三方组件、模型和FFmpeg许可未逐项核实。
- 最终展示版本、全部AI对话记录、队伍授权确认等提交材料仍需队伍补齐。

## 7. 继续开发建议

1. 在GitHub评审 `feat/local-model-audio-fix`，通过后合入main。
2. 让 `integration/28-reconciliation` 合并最新main，运行model-free测试后再处理旧分支。
3. 用真实设备做一次10–20分钟短会，重点观察“PCM时间戳不连续”是否消失、定稿延迟和漏句。
4. 为 `data/clips`/`data/pool` 建立按项目归档策略；任何清理先做清单和备份。
5. 为外部模型与FFmpeg建立独立版本/许可清单，不将权重和二进制放入git。