# 本机部署与首次实测

更新：2026-10-13。原仓库是 [DanielXiong8/meetingVAR](https://github.com/DanielXiong8/meetingVAR)。
本机部署使用 `codex/local-acceptance-fixes`；main 尚未合并 PR #28。
已拉取另外的 `pr-b/pr-c` 供查看，它们的高清限额、词表治理改动尚未整合进此部署。

## 先掌握这条主线

**项目资料 → 自动生成监听词 → 系统声音与屏幕缓冲 → 相关话题或回车触发 → 保存证据片段 → 纪要 → 人工评价 → 下一次会议。**

当前产品是 Windows 命令行应用。双击仓库根目录的 `Start-MeetingVAR.cmd` 打开本机菜单。
它使用独立的 `.venv-live` Python 3.10 环境，模型在仓库 `models/`，会议产物在 `data/`。

| 想理解什么 | 从哪里开始 |
|---|---|
| 功能、流程及项目定位 | [README 的架构和快速开始](../README.md#架构) |
| 操作与验收步骤 | [完整使用清单](USAGE_CHECKLIST.md)；本页的首次实测 |
| 项目资料怎样变为词表 | [memory/extract.py](../src/meetingvar/memory/extract.py)、[monitor/hotwords.py](../src/meetingvar/monitor/hotwords.py) |
| 会中如何缓存、识别、触发和保存 | [live.py](../src/meetingvar/live.py)、[pipeline.py](../src/meetingvar/pipeline.py) |
| 纪要怎样生成、评价怎样影响下一场 | [report/generate.py](../src/meetingvar/report/generate.py)、[memory/calibration.py](../src/meetingvar/memory/calibration.py) |

最常用的功能是 `go`：给一个包含 `.md/.txt` 的项目文件夹，即完成抽词、实时监听、结束后纪要与项目归档。
`replay` 用现有 WAV 测识别与触发；`review` 评价片段；`rescan` 更新项目词表。

## 第一次尝试：两分钟

1. 双击 `Start-MeetingVAR.cmd`，输入 **1** 查看环回设备，确认标注“系统默认”的设备正是你正在听的扬声器或耳机。
2. 输入 **2** 开始 120 秒实测。菜单会将演示资料复制到 `data/trial_project/project.md`，此后可以直接编辑。
3. 在播放器中播放一段有语音的音频、视频或会议声音。部署自检的官方短音频位于
   `data/deployment/official_asr_example_zh.wav`，也可以播放它来尝试。
4. 等待终端出现带时间和说话人标签的转写；默认每 8 秒处理一块，首次需要加载四个模型。
   说出资料中列出的监听词可测试自动触发。**按回车可保存手动 VAR**，建议首次至少按一次。
5. 两分钟后自动结束；也可按 **Ctrl+C** 提前结束。等待终端完成收尾和报告生成。
6. 菜单输入 **4** 打开最新报告目录，查看 `minutes.md` 和 `var_reports.md`。

**声音来源是系统播放混音。只对着本机麦克风说话不会被采集。**
来自其他参会者、播放器或另一台设备连入会议后经本机扬声器/耳机播放的声音可用于测试。

自动词表来自演示资料；官方样例内容与演示主题可能不同，回车触发可以独立验证保存流程。
没有任何触发时，本场不会保存会议内容，也不会生成新纪要；菜单 4 可能显示上一场报告，请核对会话 ID。
`spk0/spk1` 是模型在局部音频内给出的匿名编号，不代表跨窗口的固定真人身份。

## 换成自己的项目

菜单输入 **3**，粘贴项目资料文件夹的完整路径，例如 `D:\Projects\MyProject`。
准备几份简短的 `.md/.txt`，写明主题、术语、人员和待办，有助于判断相关内容。

也可以在仓库根目录的 PowerShell 中执行：

```powershell
.\Start-MeetingVAR.cmd -Project 'D:\Projects\MyProject'
# 指定时长、只采音频或用 CPU：
.\Start-MeetingVAR.cmd -Project 'D:\Projects\MyProject' -Duration 180 -NoScreen -Device cpu
```

首轮使用常规低帧率屏幕缓冲；本启动菜单默认不启用高清 `--hd`。
此部署的高清模块仍可能产生大量 raw 临时帧，需按完整清单核对空间后再另行使用。

菜单 **5** 可以把现有 WAV 送入真实语音管线。完成后记下终端输出的 session ID，再生成纪要：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:MEETINGVAR_MODELS=(Join-Path $PWD 'models')
$Python=(Resolve-Path '.\.venv-live\Scripts\python.exe').Path
& $Python -m meetingvar digest --session '<session ID>'
& $Python -m meetingvar review --session '<session ID>' --summary
```

默认 `go` 生成结构化模板纪要。显式 `--llm` 使用已有配置的本地或云端服务，云端会接收相应文本；
需要语义版时按 [完整清单](USAGE_CHECKLIST.md) 的 LLM 步骤操作。

## 文件在哪里

| 内容 | 位置 |
|---|---|
| 片段触发原因、会话转写和摘要 | `data/pool/<session ID>/` |
| 可回听 WAV、屏幕 JPEG 和清单 | `data/clips/<clip ID>/` |
| 薄纪要、VAR 报告 | `data/reports/<session ID>/` |
| 项目内的纪要副本 | `<项目>/meetingvar/minutes/<session ID>/` |
| 项目监听词 | `<项目>/meetingvar/terms.txt` |
| 显式评价后的兴趣阈值 | `<项目>/meetingvar/calibration.json` |
| 本机部署与真实自检证据 | `data/deployment/` |

媒体、模型、环境和密钥均在 Git 忽略范围；分享问题时使用会话 ID 与脱敏日志。

## 部署基线与复现

本机显卡为 RTX 5070 Ti Laptop 12GB。本次首测采用 **torch 2.7.1 / torchaudio 2.7.1 CPU 版**；
启动菜单默认 `-Device cpu`。大型 CUDA 包下载耗时较长，已停止该下载；GPU 加速尚未部署或验证。
普通 Python 依赖使用清华镜像源 `https://pypi.tuna.tsinghua.edu.cn/simple`，
PyTorch CPU 包来自 [PyTorch 官方历史版本页](https://pytorch.org/get-started/previous-versions/#v271) 对应下载源。
语音栈是 SenseVoiceSmall、FSMN-VAD、CAM++、CT-Transformer，参见 [FunASR 官方项目](https://github.com/modelscope/FunASR)。

本机已完成的真实自检（2026-10-13）：

- 四套模型的权重、配置与词表按官方 SHA-256 核验，清单为 `data/deployment/models.json`。
- CPU 真实识别得到“欢迎大家来体验达摩院推出的语音识别模型。”，首次包含导入和模型加载共 39.52 秒。
- 真正运行 `go --device cpu --duration 20`，用 Windows 系统播放官方样例并从 WASAPI 环回采集，
  同时录屏、发送一次手动回车触发。会话为 `go_1791905573856678200`，生成 7 个片段，L1/L2/L3 均出现。
- 保存的 WAV 有非零语音信号，每个片段有 10–58 张真实屏幕关键帧；薄纪要、VAR 报告和项目归档均存在。
- 新真实环境的完整回归 **107 项通过，0 失败、0 跳过**，编译和独立仿真通过；
  日志为 `data/verification/run_1791905771655260100/`。`pip check` 通过。
- 当前系统默认端点是 **扬声器 (ToDesk Virtual Audio) [Loopback]**。切换输出或耳机后先用菜单 1 重新确认。
- 实时重叠尾块额外出现“别模型。”碎片；这次证明真实链路可运行，不代表识别准确率或触发质量已经验收。

完整证据为 `data/deployment/runtime_check.json`、`real_cli_go.log`、`real_asr.json`；
实际安装快照为同目录的 `requirements-lock.txt`。真实会议仍按完整使用清单逐项验收。

重新安装 CPU 环境的入口（新电脑先准备 Python 3.10；本机已经装好）：

```powershell
py -3.10 -m venv .venv-live
$Python=(Resolve-Path '.\.venv-live\Scripts\python.exe').Path
& $Python -m pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cpu
& $Python -m pip install -e . -r requirements-live-cpu.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
& $Python -m pip check
```

模型位于本机 `models/`，不随 Git 克隆。上述安装命令不自动下载权重，
另一台电脑需另行准备同一模型栈并校验；本机可直接使用现有模型。

如果双击报缺依赖或找不到模型，请先核对 `.venv-live` 和 `models`，再查看完整使用清单中的环境准备。
不要使用系统 Python 3.14 启动本项目。
