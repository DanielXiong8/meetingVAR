# meetingVAR 开发资源与分支状态（2026-10-19）

本文用于黑客松结束后的持续开发交接。只记录资源位置、当前状态和已知边界，不包含API密钥、真实会议转写或用户配置内容。

## 1. GitHub 分支拓扑

远端：<https://github.com/DanielXiong8/meetingVAR>

| 分支 | 当前提交 | 状态与建议 |
|---|---|---|
| `main` | `91aa4dc` | 已合并 PR #34（`feat/desktop-ui`），是当前主线基线。 |
| `feat/local-model-audio-fix` | `f822206` | 0.1.3本地GPU/API诊断＋双源音频时间轴＋外部API截断修复；待评审合入。 |
| `feat/agent-runtime` | `372ad2f` | Agent 闭环 + 0.3.0 ReAct Runtime + 0.4.0 ZCode 式深色工作区；已推送并打 `v0.2.1`/`v0.2.2`/`v0.3.0`/`v0.4.0` tag。 |
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
| 工作仓库 | `C:\Users\Lenovo\projects\meetingVAR` | 分支 `feat/agent-runtime`，版本0.4.0。 |
| 引擎环境 | `D:\miniconda3\envs\meetingvar` | Python 3.10，editable安装指向工作仓库；修改引擎代码后下次启动生效。约6.8GB。 |
| 桌面安装 | `C:\Users\Lenovo\AppData\Local\Programs\meetingVAR` | 已安装0.4.0；注册表版本、快捷方式与 `--selftest` 已核验。 |
| 配置与日志 | `C:\Users\Lenovo\AppData\Roaming\meetingVAR` | `ui_config.json`、UI/模型日志及旧配置备份；含本机路径，不入库。 |
| 本地模型根目录 | `D:\env\models\models` | 约11.8GB，含Qwen3-4B GGUF、Qwen3-4B AWQ/FP8、SenseVoice、Paraformer、CAM++等。 |
| 原生推理后端 | `D:\env\llama.cpp\b11669-vulkan-x64` | 官方Vulkan版llama-server，支持本机RTX 5060；不随源码或UI安装包分发。 |

## 3. 构建、安装包与提交材料

| 资源 | 位置 | 说明 |
|---|---|---|
| 0.1.1/0.1.2/0.1.3发布目录 | `C:\Users\Lenovo\.zcode\workspace\default\meetingvar_release_0.1.*` | 历史版，各约65MB。 |
| 0.2.x/0.3.0/0.4.0发布目录 | `meetingvar_release_0.2.*`、`meetingvar_release_0.3.0`、`meetingvar_release_0.4.0` | 0.4.0 为当前版，含 ZCode 式深色 UI 与企业级 Agent Runtime v2；0.2.x/0.3.0 保留旧版。 |
| 仓库旧构建产物 | `meetingVAR/dist`、`dist_installer`、`build` | 早期0.1.0产物，建议确认后归档或清理；不要与0.4.0混淆。 |
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

## 5. 验证快照（2026-10-20 上午，0.4.0）

- 完整Python3.10依赖环境：453项测试、**0跳过、453通过**（66.20秒），`compileall`通过，完整CLI仿真通过（15.02秒）。
- 最小依赖CI模拟：453项发现，412项通过，41项按依赖跳过，编译通过。
- 提交包内音频专项：24项通过，含90分钟虚拟双源到RTASR发送链路。
- Agent Runtime v2：模型原生 function calling、有界 ReAct/Plan-Execute（最多 8 步）、Principal/项目作用域、审批 token、步数/工具/token/时间预算、引用校验、LLM 级 Trace、断点续跑、黄金问题集评测。CLI `agent-run`/`agent-eval`，桌面 Agent 页新增“Agent 深度检索”。
- Agent专项：37项通过（工具审批/超时/幂等、Trace脱敏、检查点、事实记忆、五类事实核对/审批/dry-run、记忆问答、会前关注清单、CLI）。
- 0.2.1真实窗口复测：本地模型生成并保存关注清单、19个有转写会话过滤、0事实问答、会后0候选与模型停机错误提示、设置页本地/自定义厂商验证、短会开始/结束（RTASR连接、8句字幕、录屏缓冲约7.8MB、零命中丢弃）均通过。详见 `docs/BUGFIX_0.2.1.md`。
- 0.2.1安装包：`meetingVAR-setup-0.2.1.exe` SHA256 `467eb81aecf04aab5c828c148967eec8ff71c0d816510f2fa019d3c0a74f8cde`；内置 `meetingVAR.exe` SHA256 `3cc0f8512178d9b6310df2c5b93bf7667315bc04162ca6a5cabdf668e4fc5950`；安装后 `--selftest` 通过。
- 0.2.2全库理解冷启动：清空项目 `meetingvar/understand` 与 `interest_context.json` 后，真实 UI 对 `D:\项目\数据结构` 读完 **14/14 块、60156/60156 字、100%**，写入 `interest_context.json`；其中密集合块自动降级为精简关键词模式。详见 `docs/BUGFIX_0.2.2.md`。
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