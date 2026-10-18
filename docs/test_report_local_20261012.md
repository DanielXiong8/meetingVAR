# 本机模拟验收与修复报告 · 2026-10-12

## 来源与范围

实际仓库：`meetingVAR-b-feedback-loop`，远端 `DanielXiong8/meetingVAR`。
同步时主线仍为 `c5777a5`，原工作分支为 `628e77f`；发现新远端分支 `pr-a`（`57ed2c1`），
其 `docs/decision_log.md` 记录了协作者的验收缺陷及修复。本轮在 `codex/local-acceptance-fixes`
整合这两条分支，保留双方历史与当前的反馈校准、仿真、环境加载和加密分享功能。

本机为 Windows，独立 Python 3.10.22 环境；ffmpeg/ffprobe 位于 `C:\Program Files\ffmpeg\bin`。
本轮执行真实 CLI、文本处理、音频转换、池写入、纪要、反馈校准和项目归档；ASR 模型输出、
WASAPI 输入、屏幕源采用可控替身。HD 使用假屏幕，但真实运行本机编码器并验证生成视频。

## 修复前的独立复现

将协作者的测试拿到原工作分支运行，先记录失败，再整合修复：

| 检查 | 本机修复前结果 | 证据 |
|---|---|---|
| 持久化策略 | 6 项中 3 项失败 | 静音仍建目录；有转写无命中仍存全文；随后 digest 制造纪要 |
| 事件匹配边界 | 22 项中 5 项失败 | 乱序合并错误；非法时间/时长未拒绝 |
| HD 生命周期 | 5 项中 1 项失败 | ffprobe 报 `moov atom not found`；控制台却打印成功，原始帧已删除 |

日志分别在本机 `data/before_persistence_workspace_20261012.log`、
`data/before_matching_workspace_20261012.log`、`data/before_hd_workspace_20261012.log`。
这些是合成输入的工程复现，没有把真实会议材料提交到 Git。

最初直接使用受限执行环境的系统临时目录时出现 PermissionError；本机 HTTP 桩服务也受到沙箱网络限制。
前者通过项目内独立临时目录解决；后者在允许本机回环服务的执行权限下重跑。
这两类基础设施失败单独保留日志，没有当成产品缺陷或通过结果。

## 最终行为与新增验证

1. 零命中时不建会话目录、不保存全文、不凭空生成纪要；命中时保留片段及薄纪要所需的全场转写文本。
2. 环形缓冲按秒分格并保留尾块，回溯两端按采样点裁剪；305.5 秒触发仍保留完整 300 秒。
   断流两侧不错误拼成连续 ASR 输入，保存音频以静音标记缺失时间；倒退或重叠输入明确拒绝。
3. HD 的 raw RGB 帧采用正确解码参数，检查编码返回码、文件及完整解码后才删源帧；失败时保留源帧。
   记录尺寸、帧数和实际帧率，并定期更新恢复元数据；奇数分辨率补齐到编码器支持的尺寸。
4. `record/go` 共用设备生命周期，初始化或 finish 失败仍释放已打开的音频、屏幕与 HD 资源。
   启动时处理已有音频缓冲，退出时冻结采集并排空尾块，避免只取循环轮询期间的数据。
5. 静音期间回车可以保存已到达的缓冲，无需等待下一次 ASR；采集仍在主线程中处理，推理期间的按键需等待推理返回。
6. 默认跟随系统默认播放端点，移除无法取消的逐端点探测线程，增加 `go/record --audio-device`。
   音频打开失败释放 PortAudio；录屏后端初始化失败向调用方报错。
7. 多次 `rescan` 不再丢掉 `#!keep` 标记；空标记安全忽略。后台词表增强同步更新纠错与监听词。
8. 会话 ID 使用纳秒标识，降低同秒连续调用复用目录的风险。新增 `archive` CLI 更新已有项目纪要副本，
   修正文案中“重新 digest 会自动覆盖项目归档”的不实提示。
9. 合并时发现 pool 反馈摘要仍使用 `time.strftime`，补回被另一分支移走的 `time` 导入；完整仿真验证反馈闭环没有退化。

Clip JSON 字段没有新增或改名；时间字段说明更新为当前实现保存的 WAV 物理窗口。

## 最终执行结果

| 检查 | 最终本机结果 |
|---|---|
| 完整轻量依赖环境 | **107 项通过，0 失败，0 跳过**；43.41 秒 |
| 无可选依赖的独立环境 | 107 项发现，**89 项通过、18 项按缺少可选依赖跳过**，0 失败；35.12 秒 |
| 编译检查 | src / scripts / tests 全部通过 |
| 独立端到端仿真 | 通过；词表 → replay → digest → review → rescan → 下一场 replay → go → 归档 |
| 首场合成输入 | 24 秒双声道转换为 16k 单声道，4 句转写、8 个片段，L1/L2/L3 全覆盖，111 个 JPEG 可解码 |
| 回溯边界 | 30 种输入形状/触发时间组合、900 秒长输入、非整秒 300 秒窗口，以及断流/重叠输入回归通过 |
| HD | 本机 ffmpeg 编码、完整解码和 ffprobe 帧数/时长/尺寸检查通过；失败、损坏和无编码器均保留源帧 |
| 实时故障与流程回归 | 13 项通过，包括初始化失败清理、保留 ASR 原始异常、启动游标、退出尾块、静音手动 VAR、词表保留和归档更新 |
| 真实模型审计 | **3 项全部跳过**，缺模型和指定 WAV |
| 真实时序校准 | **2 项全部跳过**，缺模型和指定 WAV |

最终完整结果在本机 `data/verification/run_1791819364358438400/`，包含 `result.json`、
`unittest.log`、`simulation.log`、编译与模型审计日志，以及 `simulation/run_*/` 的可检查媒体与项目归档。
最小环境结果在 `data/verification/run_1791819363995326700/`。
前述真模型跳过项单独列出，没有混入“107 项通过”的数字。

## 复现与实测入口

```powershell
Set-Location 'C:\Users\QYC\Documents\ChatGPT\with_xiong\meetingVAR-b-feedback-loop'
.\.venv\Scripts\python.exe scripts/run_local_checks.py --with-model-audits
# 已有无可选依赖环境时，可另验证最小安装的降级路径：
.\.venv-smoke\Scripts\python.exe scripts/run_local_checks.py --skip-simulation --isolated-cwd
```

脚本不修改 `.env`，不调用真实云端 LLM，子进程固定测试配置。
详细安装、真实 WAV、系统声音演练、会议操作、回听、评价与反馈步骤见
[使用与验收清单](USAGE_CHECKLIST.md)。

## PR smoke 的 Windows 编码故障与修复

原提交 `068e06a` 的 [smoke 运行](https://github.com/DanielXiong8/meetingVAR/actions/runs/38271803184)
中，`model-free` 与 Ubuntu 仿真通过，Windows 仿真发现 107 项测试，报告 11 个错误、1 项跳过。
11 个错误均为 `UnicodeEncodeError`：英文 Windows runner 的重定向输出默认使用 `cp1252`，
无法打印中文诊断，导致 HD 降级、LLM 回退、零命中 digest、go 清理等测试在提示输出处失败。
原本机测试脚本启用了 UTF-8，但旧 CI 直接调用 unittest，没有同步该配置。

修复后，工作流显式设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，两个作业都调用
`scripts/run_local_checks.py`，统一回归、编译、仿真和配置隔离。脚本同时覆盖继承的
`PYTHONIOENCODING`，避免它优先于 `PYTHONUTF8` 生效而重新引入非 UTF-8 输出。
CI 无论检查成功或失败都上传 JSON 结果与日志；没有移除或跳过失败测试。

本机复核证据：

- 选择 HD 缺编码器、LLM 不可用回退、零命中 digest 三项原失败测试，
  `PYTHONUTF8=0` / `PYTHONIOENCODING=cp1252` 下复现 3 个编码错误；
  改用上述 UTF-8 配置后 3 项全过。日志在 `data/verification/encoding_1791820349319385500/`。
- 完整轻量环境：**107 项通过，0 失败、0 跳过**，44.31 秒；编译与独立仿真通过。
  结果在 `data/verification/run_1791820369217068100/`。
- 最小环境：发现 107 项，89 项通过、18 项按缺可选依赖跳过，0 失败，35.61 秒；编译通过。
  故意继承 `PYTHONIOENCODING=cp1252` 运行脚本，确认子进程仍使用 UTF-8。
  结果在 `data/verification/run_1791820368700686900/`。

远端状态以 [PR #28 最新检查](https://github.com/DanielXiong8/meetingVAR/pull/28/checks) 为准；
CI 缺少 ffmpeg 等可选依赖时的跳过项应与本机完整通过结果区分。

## 尚未完成的真实验收

- 本机轻量环境没有 FunASR/torch/PyAudioWPatch/dxcam/mss，也没有已验证的本地模型权重。
  真实模型审计 3 项、真实时序校准 2 项因缺少模型和素材跳过，**不得记为通过**。
- 未验证真实 WASAPI/显示器采集、真实语音识别、多说话人归因、GPU、长会议稳定性、真实 LLM 输出质量或云端费用。
- 高清仍使用 raw 临时帧，2560×1600、30fps、90 秒理论量级约 33GB；本轮没有改存储策略。
  真机先做短补录并核对剩余空间；MP4 不带音轨。
- 当前来源仅为系统播放混音，不额外采集本机麦克风；语音模型跨窗口说话人编号不能当作真人身份保证。
- 确定性工程样例不是独立质量评测集；不报告真实 Precision/Recall/F1 或“训练提升”。
  当前事件评估读取媒体回溯区间，长回溯可能合并邻近事件，正式评测需要说明此口径。
- 进程强杀、断电、驱动挂起与操作系统级全部写盘行为未做验收；元数据定期保存不等于强杀恢复保证。
