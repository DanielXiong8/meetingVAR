# meetingVAR 使用与真实会议验收清单

更新：2026-10-12；对应分支 `codex/local-acceptance-fixes`。本清单适用于当前 CLI 版本。

## 1. 找对仓库和 Python

本机实际 Git 仓库是 `C:\Users\QYC\Documents\ChatGPT\with_xiong\meetingVAR-b-feedback-loop`。
旁边的 `meetingVAR-main-latest`、`meetingVAR-codex-fix-l2-interest-scoring` 是旧文件副本，没有各自的 Git 历史；请使用实际仓库。

PowerShell 中执行：

```powershell
Set-Location 'C:\Users\QYC\Documents\ChatGPT\with_xiong\meetingVAR-b-feedback-loop'
git status --short --branch
git fetch origin
git switch codex/local-acceptance-fixes
git pull --ff-only
$Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$env:PYTHONUTF8='1'
& $Python --version
& $Python -m meetingvar --help
```

- [ ] 当前分支正确，更新完成。若 Git 提示本地文件有改动，先保留自己的工作，不使用 `reset --hard`。
- [ ] Python 是 **3.10.x**。本机 `.venv` 是 **3.10.22**；系统 `python` 是 3.14，直接敲 `python` 会用错环境。
- [ ] 所有命令从仓库根目录执行。`.env` 和 `data/` 相对于当前目录，不会自动搜索父目录。

## 2. 先跑本机仿真

本机已有轻量仿真环境，可直接执行：

```powershell
& $Python scripts/run_local_checks.py --with-model-audits
```

新电脑需先用 Python 3.10 建环境，再安装轻量依赖：

```powershell
py -3.10 -m venv .venv
$Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $Python -m pip install -e . -r requirements-simulation.txt -r requirements-secrets.txt
& $Python scripts/run_local_checks.py
```

- [ ] `unittest` 成功、`compile` 成功、`simulation` 成功。2026-10-12 本机完整回归为 **107 项通过，0 跳过**。
- [ ] 打开脚本最后输出的 `data/verification/run_*/result.json`，核对每个检查的退出码和 `skipped` 数。
- [ ] 查看同目录的 `unittest.log` 和 `simulation.log`。合成 WAV/JPEG、纪要及反馈归档在该目录的 `simulation/run_*/` 下。
- [ ] 确认理解测试边界：仿真使用编码信号和可控模型输出，验证真实工程管线；它不能证明真实语音识别率、声卡、显示器、GPU 或会议触发质量。

脚本只在测试子进程中清空云端 LLM 设置，使用独立可写临时目录，不修改 `.env`。
默认测试包含本机 `127.0.0.1` HTTP 桩服务；受限执行环境可能禁止该访问，需要允许本机回环通信后重跑。
`--with-model-audits` 另外执行真实模型审计；缺少模型或指定 WAV 时会显示 **skipped**，不得算作通过。

## 3. 真实采集前的环境准备

**本机当前 `.venv` 可跑仿真，但未安装 FunASR、torch、PyAudioWPatch、dxcam、mss，且没有已验证的本地模型栈。**
请先准备独立的真实采集环境，避免把轻量测试环境当作完整会议环境：

```powershell
py -3.10 -m venv .venv-live
$LivePython = (Resolve-Path '.\.venv-live\Scripts\python.exe').Path
& $LivePython -m pip install -e . -r requirements.txt
& $LivePython -m pip check
```

这是仓库的安装入口，**这次没有在本机下载安装 GB 级依赖/权重，也没有验证全套依赖解析**。
GPU 安装需按实际显卡选择匹配的 torch/torchaudio；[SETUP.md](../SETUP.md) 中 RTX 5060、D 盘 conda 路径属于协作者历史机器记录，不能直接当成本机配置。
首次验收可先使用 `--device cpu`，跑通后再切换 GPU。

- [ ] 环境含 `torch`、`torchaudio`、`funasr`、`modelscope`、`PyAudioWPatch`；需要录屏时还要 `Pillow`、`mss`，Windows 优先后端 `dxcam` 还需 `opencv-python-headless`。
- [ ] 用 `MEETINGVAR_MODELS` 指向实际权重目录，不能凭目录存在就认定模型下载完整。
- [ ] 该目录下有可加载的 **SenseVoiceSmall / fsmn_vad / campplus / punc_ct-transformer** 四套模型，包含配置及权重，无遗留不完整下载文件。
- [ ] 首先用短 WAV 完成下一节的真实 `replay`，确认四模型组合能加载。
- [ ] 需要高清补录时，`ffmpeg` 可发现。本机位置为 `C:\Program Files\ffmpeg\bin\ffmpeg.exe`；测试还用同目录 `ffprobe.exe` 检查媒体。

设置模型目录的示例（替换为你的实际目录）：

```powershell
$env:MEETINGVAR_MODELS='D:\你的模型目录'
& $LivePython -c 'from meetingvar import config; print(config.MODELS_ROOT); [print(config.find_model_dir(k)) for k in (config.ASR_MODEL_KEYWORD, config.VAD_KEYWORD, config.SPK_KEYWORD, config.PUNC_KEYWORD)]'
```

## 4. 准备项目记忆与监听词

使用只包含本项目材料的文件夹。程序扫描 `.md`/`.txt`，自动排除虚拟环境、依赖和运行时目录。

```powershell
$Project='D:\你的项目文件夹'
& $LivePython -m meetingvar terms --memory $Project --out data/my_terms.txt
Get-Content data/my_terms.txt
```

- [ ] 检查输出词表，保留本次会议真正关心的术语和人名；准备 2–3 个确定会说到的测试词。
- [ ] 需要人工补词时，创建 UTF-8 文件，每行一个词，例如 `测序污染`、`重测`，然后加 `--extra`。
- [ ] 手动永久保留词可使用 `#!keep 你的词`；空的 `#!keep` 会安全忽略。
- [ ] 默认短句过滤阈值为 2，单字“是/好/停”不参与转写或触发。确有需要时，在启动前设置 `$env:MEETINGVAR_MIN_TEXT_CHARS='1'`，同时会纳入更多噪声碎片。
- [ ] L2 使用项目文档的 TF-IDF 相似度；不等于已经验证准确率的语义模型。项目文件空时主要依靠监听词和手动 VAR。

`go` 自动从项目重新抽词；如果必须使用一个精确的人工词表，使用 `record --terms ... --memory ...`。
`go --llm`、`terms --llm`、`terms --api` 会启用配置中的 LLM；云端地址会接收相应项目文本。
不加这些选项时不启动云端语义抽取。

## 5. 用已知内容的短音频验收真实 ASR

准备 30–60 秒 WAV，包含日常发言、监听词、负责人和行动项。`replay` 支持 16-bit PCM 单/双声道 WAV；其他格式先自行转换。

```powershell
& $LivePython -m meetingvar replay --wav 'D:\测试音频.wav' --terms data/my_terms.txt --memory $Project --device cpu
```

- [ ] 模型加载成功；有可读中文转写和说话人标签。标签是模型聚类编号，不保证跨窗口一直对应同一真人。
- [ ] 说出的监听词产生 `VAR! [L1]`；项目相关发言可能产生 `[L2]`。
- [ ] 抄下输出中的实际会话 ID（如 `replay_...`），后续命令使用它。
- [ ] 打开片段 JSON 中 `audio.path` 对应 WAV，回听内容非静音、未加速/减速，片段时长与 `end_ts-start_ts` 一致。
- [ ] 回溯上限默认 300 秒，会议不足 300 秒时只能保存已到达的音频。屏幕缓冲约 120 秒，不能期待 300 秒全程有画面。
- [ ] 无词命中的真实语音测试，应不生成该会话的文件或目录。严格控制时用与发言无交集的 `--terms`，并不传 `--memory`，避免 L2 自动命中。

`MEETINGVAR_LOOKBACK` 应在 1–600 秒内；`MEETINGVAR_CHUNK` 默认 8 秒，重叠 3 秒。
识别与触发存在模型加载和推理延迟，控制台开始监听不代表 ASR 已经预热完成。

## 6. 先做 2 分钟系统声音演练

```powershell
& $LivePython -m meetingvar devices
& $LivePython -m meetingvar record --terms data/my_terms.txt --memory $Project --duration 120 --device cpu
```

- [ ] 在系统默认输出设备播放包含测试词的语音；确认耳机或扬声器实际有声音。
- [ ] `devices` 中 `[系统默认]` 与会议软件实际输出一致。当前版本直接跟随系统默认输出，不再逐端点试开流。
- [ ] 若会议软件指定了另一设备，用 `--audio-device <编号>` 显式指定，再重跑演练。编号来自本次 `devices` 输出，不要照抄他人机器的编号。
- [ ] 有转写、有自动命中；在终端里按一次回车，出现一个 L3 手动片段。
- [ ] 回车仅在该终端获得焦点时有效，当前版本没有全局热键或托盘界面。
- [ ] 静音期间按回车也能保存已有缓冲，通常在下次主循环处理；ASR 正在推理时仍需等待推理返回。
- [ ] 按 Ctrl+C 后，出现 `[done] session=...`；设备正常释放，紧接着第二次启动不报占用。
- [ ] 验证截图是希望保存的主显示器画面；不录屏使用 `--no-screen`。

**声音来源限制**：当前采集的是系统播放混音，通常包含远端声音及其他程序的播放声音；**不会额外采集本机麦克风**。
本机自己的发言若没有经系统输出播放，就不会被录入。线下仅对着麦克风讲话不能完成当前版本的采集验收。
真实会议中先核对需要保留的发言是否确实经过该播放设备。

高清补录是另一个可选步骤：

```powershell
& $LivePython -m meetingvar record --terms data/my_terms.txt --duration 10 --device cpu --hd
```

- [ ] 先用短演练测试，发生命中后检查 `data/hd/hd_*/hd_clip.mp4` 能播放。
- [ ] 高清视频本身**不含音轨**，命中前音频在 Clip 的 WAV 中；它不是完整的带声会议录像。
- [ ] `--hd` 默认尝试 30fps、每次最多 90 秒，实际帧率记录在 `meta.json`；持续命中可产生多次补录。
- [ ] raw RGB 临时帧占用较大：2560×1600、30fps、90 秒理论量级约 33GB。先确认磁盘余量；普通测试不必开启 `--hd`。
- [ ] 编码或解码验证失败时保留 `.rgb` 与 `meta.json`，不要把残缺 MP4 当作成功；保存报错日志供排查。
- [ ] `--no-screen` 与 `--hd` 不能同时使用。结束后等待合成完成，再关闭终端。

## 7. 正式会议的命令

完成上述演练后：

```powershell
& $LivePython -m meetingvar go $Project --device cpu
# 若前面已验证 GPU 环境，则把 cpu 换成 cuda。
# 需要精确词表：
& $LivePython -m meetingvar record --terms data/my_terms.txt --memory $Project --device cpu
```

- [ ] 会议前确认声音输出设备、词表和模型目录；保持终端可见。
- [ ] 重要但没自动触发的内容，在终端按回车保存。
- [ ] 同一热词默认 300 秒去抖，不保证每次重复说该词都新建片段；L2 仍可能独立产生片段。
- [ ] Ctrl+C 正常结束，保留会话 ID 和终端日志；不要用强杀进程代替收尾。
- [ ] `go` 有命中时自动生成模板纪要并归档；`record`/`replay` 需要下一节手动 `digest`。
- [ ] 零命中时没有会议记录或纪要；命中时会保存选择的媒体片段，同时保存**全场转写文本**供薄纪要使用。

## 8. 会后回听、纪要、反馈与下一场

```powershell
$Session='粘贴本场实际会话ID'
& $LivePython -m meetingvar digest --session $Session
& $LivePython -m meetingvar review --session $Session --summary
& $LivePython -m meetingvar archive --session $Session --project $Project
```

文件位置：

| 产物 | 路径 | 核对内容 |
|---|---|---|
| 全场转写/总结计数 | `data/pool/<session>/transcript.json`、`summary.json` | 时间、句数、触发层和片段 ID |
| 片段详情 | `data/pool/<session>/<clip_id>.json` | 触发原因、音频路径、截图引用、评价 |
| 命中音频 | `data/clips/<clip_id>/<clip_id>.wav` | 回溯内容和时长 |
| 低帧率截图 | `data/clips/<clip_id>/screens/` | JPEG 可查看，manifest 时间为会议相对秒 |
| 高清补录 | Clip 的 `screen.hd_clip_dir` | MP4 / raw 帧 / meta，与对应 Clip 的目录一致 |
| 薄/厚纪要 | `data/reports/<session>/minutes.md`、`var_reports.md` | 模板状态、行动项与原始证据相符 |
| 项目归档 | `<项目>/meetingvar/minutes/<session>/` | 当前生成的 Markdown 副本 |
| 评价和校准 | `data/pool/<session>/feedback.json`、`<项目>/meetingvar/calibration.json` | 只将显式 L2 评价用于项目阈值 |

默认厚纪要是填好证据上下文的 prompt 模板，须人工阅读；它不是模型生成的最终总结。
要生成叙述版，按 [.env 配置与加密分享说明](encrypted_env.md) 配好服务后执行：

```powershell
& $LivePython -m meetingvar digest --session $Session --llm
& $LivePython -m meetingvar archive --session $Session --project $Project
```

`digest` 会覆盖 `data/reports/` 中同名 Markdown；**不会自动刷新之前的项目归档**，所以再执行 `archive`。
云端服务会接收本次纪要生成所需文本；默认测试脚本不调用真实云端 API。

评价反馈：

```powershell
& $LivePython -m meetingvar review --session $Session
# 或指定唯一片段 ID 前缀；替换下面的示例 ID：
& $LivePython -m meetingvar review --session $Session --clip '实际片段前缀' --status useful --note '这段需要回听' --project $Project
& $LivePython -m meetingvar review --session $Session --summary --project $Project
& $LivePython -m meetingvar rescan $Project
```

- [ ] 对有用和没用片段都评价；校准时必须指定 `--project`。重复汇总不会重复计数。
- [ ] 校准仅影响该项目 L2 阈值，不改变 L1 和 L3；不是训练模型。
- [ ] `rescan` 把归档纪要加入下一次词表来源；`#!keep` 手动词在多次重扫后仍保留。
- [ ] 下一场使用相同项目目录，检查输出的 L2 阈值是否与校准文件一致。

## 9. 故障速查与反馈清单

| 现象 | 优先检查 |
|---|---|
| Python 版本不支持、`audioop` 缺失 | 是否误用系统 Python 3.14；使用 `.venv` / `.venv-live` 的 Python 3.10 |
| 找不到 funasr/torch/pyaudiowpatch | 是否只装了仿真依赖；按第 3 节准备真实环境 |
| 找不到模型、加载失败 | `MEETINGVAR_MODELS`、四套配置及完整权重；不要复制协作者机器路径 |
| 全程无转写或静音 | 实际播放设备、会议软件输出、系统音量；使用 `--audio-device`；本机麦克风不在当前采集源中 |
| 录屏启动失败 | mss/dxcam/Pillow 是否安装，桌面是否可捕获；可先用 `--no-screen` 验证音频 |
| 说了词但没新片段 | 转写是否认对、词表是否包含、300 秒去抖、单字过滤；先手动回车保存 |
| 无纪要、digest 提示没有内容 | 本场是否零命中；零命中按策略不保存，无法会后恢复草稿 |
| 高清失败/停留在合成 | ffmpeg 是否发现、剩余空间、日志；失败源帧保留，不能直接删 |
| 重新 digest 后归档仍是旧内容 | 再执行 `archive --session ... --project ...` |
| 仿真大量 PermissionError/本机 HTTP 请求失败 | 使用 `scripts/run_local_checks.py` 的独立临时目录，允许本机回环测试服务 |

遇到异常请记录：

- [ ] Git 分支、`git rev-parse HEAD`、Python 版本、完整命令和会话 ID。
- [ ] 期望发生什么、实际发生什么、发生在第几秒、能否重复。
- [ ] `devices` 输出、实际会议播放设备、CPU/GPU、是否开录屏/高清/LLM。
- [ ] 终端错误及 `summary.json` 中非敏感计数，必要时人工摘录相关 Clip 字段。
- [ ] 测试清单：短 WAV、2 分钟采集、手动 L3、退出后再次开录、超过 300 秒回溯、会后评价再开下一场。

`data/`、模型、真实会议媒体、`.env`、`.secrets/` 不入 Git。提交反馈时可在 `docs/` 写脱敏复现步骤和错误摘要。
真实会议的长时间稳定性、识别率、跨窗口说话人归因和触发质量，仍以你下一轮实测结果为准。
