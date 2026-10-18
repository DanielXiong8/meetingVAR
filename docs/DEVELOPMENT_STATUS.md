# meetingVAR 开发资源与分支状态（2026-10-18）

本文用于黑客松结束后的持续开发交接。只记录资源位置、当前状态和已知边界，不包含API密钥、真实会议转写或用户配置内容。

## 1. GitHub 分支拓扑

远端：<https://github.com/DanielXiong8/meetingVAR>

| 分支 | 当前提交 | 状态与建议 |
|---|---|---|
| `main` | `91aa4dc` | 已合并 PR #34（`feat/desktop-ui`），是当前主线基线。 |
| `feat/local-model-audio-fix` | `64dcbe0` | 本次新增：0.1.3本地GPU/API诊断＋双源音频时间轴修复。基于最新main，待评审合入。 |
| `integration/28-reconciliation` | `af92813` | 含队友PR #32/#33；相对main有19个main独有提交、2个该分支独有提交，已分叉，合入前需先合并/变基最新main。 |
| `feat/desktop-ui` | `a9abea5` | 已由PR #34合入main，可保留作历史或清理。 |
| `feat/term-curator` | `1e778c7` | 已并入integration，旧分支。 |
| `fix/speaker-diarization` | `77569d8` | 已由PR #32并入integration，旧分支。 |
| `pr-a` / `pr-b` / `pr-c` | 57ed2c1 / 2bdc1a4 / 7549190 | 已完成的历史PR分支。 |
| `codex/b-feedback-loop` | `628e77f` | 10月11日旧实验分支。 |
| `codex/local-acceptance-fixes` | `0a16d53` | 10月13日旧验收分支。 |

**建议顺序：** 先评审并合入 `feat/local-model-audio-fix`；再由队友把 `integration/28-reconciliation` 合并最新main解决分叉；确认无回归后统一旧分支的清理。删除远端分支会改变协作者环境，本文不自动执行。

新建PR入口：<https://github.com/DanielXiong8/meetingVAR/pull/new/feat/local-model-audio-fix>

## 2. 本机代码与运行环境

| 资源 | 位置 | 当前状态 |
|---|---|---|
| 工作仓库 | `C:\Users\Lenovo\projects\meetingVAR` | 分支 `feat/local-model-audio-fix`，工作区干净，版本0.1.3。 |
| 引擎环境 | `D:\miniconda3\envs\meetingvar` | Python 3.10，editable安装指向工作仓库；修改引擎代码后下次启动生效。约6.8GB。 |
| 桌面安装 | `C:\Users\Lenovo\AppData\Local\Programs\meetingVAR` | 已安装0.1.3；安装器版本与快捷方式已核验。 |
| 配置与日志 | `C:\Users\Lenovo\AppData\Roaming\meetingVAR` | `ui_config.json`、UI/模型日志及旧配置备份；含本机路径，不入库。 |
| 本地模型根目录 | `D:\env\models\models` | 约11.8GB，含Qwen3-4B GGUF、Qwen3-4B AWQ/FP8、SenseVoice、Paraformer、CAM++等。 |
| 原生推理后端 | `D:\env\llama.cpp\b11669-vulkan-x64` | 官方Vulkan版llama-server，支持本机RTX 5060；不随源码或UI安装包分发。 |

## 3. 构建、安装包与提交材料

| 资源 | 位置 | 说明 |
|---|---|---|
| 0.1.1/0.1.2/0.1.3发布目录 | `C:\Users\Lenovo\.zcode\workspace\default\meetingvar_release_0.1.*` | 各自约65MB，保留EXE、安装器和校验/版本说明；0.1.3为当前版本。 |
| 仓库旧构建产物 | `meetingVAR/dist`、`dist_installer`、`build` | 早期0.1.0产物，建议后续确认后归档或清理；不要与0.1.3混淆。 |
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

## 5. 当前已知边界

- 音频时间轴修复已通过90分钟模拟双源到RTASR的逐字节验证；真实麦克风、讯飞实际识别质量和长会硬件稳定性仍待现场验证。
- 兴趣判断依赖自定义API；API失败不会退回热词保存，手动VAR仍可用。
- 本地模型0.1.3已验证Vulkan GPU启动与JSON生成；其他机器需按显存调整上下文、设备编号和层数。
- 项目根目录仍无项目级LICENSE；第三方组件、模型和FFmpeg许可未逐项核实。
- 最终展示版本、全部AI对话记录、队伍授权确认等提交材料仍需队伍补齐。

## 6. 继续开发建议

1. 在GitHub评审 `feat/local-model-audio-fix`，通过后合入main。
2. 让 `integration/28-reconciliation` 合并最新main，运行model-free测试后再处理旧分支。
3. 用真实设备做一次10–20分钟短会，重点观察“PCM时间戳不连续”是否消失、定稿延迟和漏句。
4. 为 `data/clips`/`data/pool` 建立按项目归档策略；任何清理先做清单和备份。
5. 为外部模型与FFmpeg建立独立版本/许可清单，不将权重和二进制放入git。