# SETUP 踩坑记录

约定：**谁踩坑谁写**，格式固定为「现象 → 原因 → 解法」，附上最终生效的命令或链接。
两个人别在同一个坑里各摔一次。

## 本轮本机验证（2026-10-12）

本机实际目录为 `C:\Users\QYC\Documents\ChatGPT\with_xiong\meetingVAR-b-feedback-loop`。
`.venv` 使用 Python 3.10.22，系统 `python` 是 3.14；下方 D 盘 conda/RTX 5060 记录来自协作者机器。
本机当前只具备轻量仿真环境，真实采集所需依赖和模型需另行准备，见 [使用清单](docs/USAGE_CHECKLIST.md)。

| 现象 | 原因 | 本轮解法 |
|---|---|---|
| 验收改动没出现在原工作分支 | 协作者修复在 `origin/pr-a`，没有并入 main | fetch 后整合 `57ed2c1`，保留双方历史并跑全部回归 |
| 未命中仍留下记录 | Pool 提前 mkdir，finish 以“没有转写”为条件 | 有片段才创建会话产物，digest/go 对无内容返回提示 |
| HD 打印成功但视频损坏 | raw RGB 当作 PNG，未检查退出码就删除源帧 | 正确解码输入，编码及完整解码验证成功才删除源帧 |
| 录屏失败后再次开录设备被占用 | 初始化不在既有收尾保护范围内 | record/go 共用 ExitStack 管理资源，初始化异常也释放 |
| 启动期间和结束前的声音缺失 | 轮询游标从初始化后的墙钟开始，未排空尾块 | 从采集起点处理已有缓冲，退出时停采并取完尾块 |
| 静音期间回车不及时保存 | 手动标记只在 ASR flush 消费 | 主循环也处理手动标记；已有缓冲可直接转正 |
| 手动词反复 rescan 后消失 | 重写文件未保留旧 `#!keep` 标记 | 每次重扫重新写入全部手动标记 |
| 更新纪要后项目副本仍旧 | digest 只覆盖 reports，不更新 archive | 新增 `meetingvar archive --session ... --project ...` |
| 测试临时目录 PermissionError | 受限环境的系统临时目录 ACL 不可写 | `scripts/run_local_checks.py` 使用 data 下独立临时目录 |

实时采集不再逐端点开探测线程，默认使用系统播放端点；需要另一端点时用 `--audio-device`。
历史“自动探测最响端点”记录已由此策略替代。完整证据见 [本机报告](docs/test_report_local_20261012.md)。

## 环境基线

- Python 3.10（conda 独立环境 `meetingvar`；FunASR 生态对 3.11+ 兼容性不稳）
- GPU：**RTX 5060 Laptop 8GB（Blackwell，sm_120）→ torch 必须 ≥2.7 的 cu128 版本**，pypi 默认装的是 CPU 版（`torch.version.cuda == None`），别混
- OS：Windows 11 26200
- **本机没有预装 conda**。已装 Miniconda 26.7.1 到 `D:\miniconda3`（静默参数 `/AddToPath=0`，不污染 PATH）：统一用全路径 `D:\miniconda3\condabin\conda.bat` 和 `D:\miniconda3\envs\meetingvar\python.exe`
- `.condarc` 已配 TUNA 镜像；pip 一律 `-i https://pypi.tuna.tsinghua.edu.cn/simple`
- C 盘仅剩 ~6GB：**模型权重/数据一律放 D 盘**。FunASR/ModelScope 下载前先设 `MODELSCOPE_CACHE=D:\env\modelscope_cache`（默认缓存会灌爆 C 盘用户目录）

## 今日踩坑（2026-10-03/04 · MVP 骨架周）

- **坑 14 续 · 环回静音根因锁定**：仓库出现**两个环回端点**（扬声器 + 耳机）。播放走默认输出（当前=耳机），采集若抓另一个端点即全静音。**record 前必跑 `meetingvar devices`，确认默认环回与播放设备一致；耳机线插拔会改变默认端点**。live record 机制已通（线程/会话/落盘正常），端到端仍静音 → 请人工看一眼系统托盘：哪个是默认输出、耳机是否真的在响
- **坑 15 · funasr 热词参数无效**（C5 实验阴性结论）：SenseVoice 与 SeACo 的 `hotword` 均不改输出（三种调用实测），转向**转写后术语纠错** `transcribe/correct.py` v4——中文（首尾音节锚定+字符重合≥0.5）、拉丁（去数字 difflib≥0.6+汉字数字尾巴转换），五用例全绿
- **坑 16 · 包内相对导入层级**：包顶层模块用 `from . import`，子包才用 `from .. import`，混用报 attempted relative import beyond top-level package

## MVP 骨架验收记录（2026-10-04）

- `meetingvar replay` **全链路通过**：18s TTS 音频 → 6 句带说话人转写 → L1 命中 4 词 → 4 个 VAR 片段落池（回溯 120s 音频）
- `meetingvar digest` 通过：薄纪要（结构化版）+ VAR 报告（模板版）落 `data/reports/<session>/`
- 纠错模块 v4 五用例全绿（含两个防误报用例）
- `meetingvar record` 机制通、端到端静音（坑 14 续，待人查设备）

## 早期踩坑（2026-09-30 → 10-01）

- **坑 1 · 全局代理劫持 git**：环境变量 `HTTPS_PROXY=http://127.0.0.1:10808` 把 git 全部流量指到本地代理。代理客户端没开时，clone/push 报 `schannel: SSL/TLS handshake failed` 或 502。临时绕过：`git -c http.proxy= -c https.proxy= push`（直连也常被掐，最好还是把代理开着）
- **坑 2 · Git Bash 参数转换**：`cmdkey /list` 在 Git Bash 里报"命令行参数不正确"——MSYS 把 `/list` 转成了路径。看凭证用 `git credential fill`（非交互须加 `GCM_INTERACTIVE=never`），别用 cmdkey
- **坑 3 · GitHub 凭证**：本机 GCM（credential.helper=manager）已缓存 DanielXiong8 的登录，clone/push 免密可用

## C1 · torch + FunASR/SenseVoice + CAM++（离线）

- 2026-10-01 02:57：**CPU 链路验收通过**——SenseVoice(936MB) + CAM++(28MB) + fsmn-vad 三模型全本地加载（`D:\env\models`），`spk_model=on` 一次成功，输出按句说话人标签 `[spk0] 0.0s …`，17 秒音频 CPU 推理 0.9s。验收脚本 `spikes/c1_asr_offline/run_offline.py`
- 坑 4：AutoModel 不挂 `vad_model` 就没有 `sentence_info`（CAM++ 白加载），说话人标签必须 `model=sv + vad_model + spk_model` 三件套
- 坑 5：SenseVoice 输出带 `<|zh|><|NEUTRAL|>` 富标签，展示层要正则剥掉
- 坑 6：ffmpeg 未装，funasr 用 torchaudio 后端加载 wav 没问题；**后续处理 mp4 音轨时必须装 ffmpeg**
- 坑 7：modelscope 大文件中断会留 `.incomplete` 且 snapshot_download 仍返回成功——**下载后必须按文件名校验**（model.pt 936,291,369 字节）
- 待办：~~cu128 torch~~ **已装（2026-10-01 03:15，torch 2.7.1+cu128，`cuda.is_available()=True`，RTX 5060 认到；funasr 兼容无恙，`--device cuda` 验收通过）**；正式拔网线验收；多说话人归因待剧本音频
- 坑 8：download.pytorch.org 直连/超时（与 GitHub 同一批境外 CDN），**cu128 wheel 从阿里云镜像 `mirrors.aliyun.com/pytorch-wheels/cu128/` 拿**；单连接被限速到 ~200KB/s，**12 段并行 Range 下载聚合 ~850KB/s**，脚本 `D:\downloads\dl_torch_resume.sh`（支持断点续传；电脑睡眠会杀后台任务，长下载前先把电源计划调成不睡眠）
- 坑 9：代理客户端（10808）国内节点正常（百度 200）但节点出口失效时 GitHub/pytorch 全 000——**换节点**即可，git 流量走 `HTTPS_PROXY` 无需额外配置

## C2 · pyaudiowpatch 系统音频环回

- **2026-10-02 验收通过（端到端）**：Realtek 扬声器环回端点（2ch@48kHz，index 10），采集期间系统播放 TTS 音频，采得 4.8MB wav（RMS=3974），送 C1 脚本转写——**转写内容含播放的句子**（"组会…污染…这篇论文对你有影响…实验设置"），"采的是系统输出而非麦克风"成立。脚本 `spikes/c2_loopback/capture_loopback.py`
- 坑 10：**环回采的是整个系统混音**——采集期间系统里播的任何声音（当时恰好在放某宣传视频）全被录进去。demo 和实际使用时要么别开别的声音，要么这是特性（"平台无关"的直接证据）
- 坑 11：播放→扬声器→环回这条回路会让转写轻微劣化（"测序"→"测试"），正是 C5 热词增强的目标场景
- 经代理访问 GitHub 间歇性 SSL 失败时重试即可（节点抖动，非配置问题）

## C3 · 腾讯会议 / 微信视频实测

（待人工：需要真实会议，排到有队友配合时做；C2 已证明系统混音采集路径，C3 预计无技术悬念）

## C4 · dxcam 低帧率录屏环形缓冲

- **2026-10-02 验收通过**：dxcam 0.3.0 在 Win11 26200 上可用（Desktop Duplication 正常），2fps 采集 8s，静态画面自动跳过未更新帧；单帧 JPEG(q70) 481KB（高分辨率屏），120 帧缓冲内存上限 ≈ 55MB，可接受。**缓冲存 JPEG 字节而非裸帧**（裸帧 1080p ~6MB/张，120 帧会爆内存）。脚本 `spikes/c4_screen/screen_buffer.py`
- 坑 12：dxcam 0.3.0 的色彩转换**强依赖 cv2**（报 ModuleNotFoundError: cv2），需装 `opencv-python-headless`

## C5 · FunASR 热词增强

- **2026-10-03 实验结论（如实记录，Issue #6 保持 open）**：
  1. **SenseVoiceSmall 的 `hotword` 参数是空操作**——开/关热词输出逐字相同（0/7→0/7）；
  2. **SeACo-Paraformer 的热词同样无效**：空格分隔字符串、裸模型、管线模式三种种法都试过，输出不变；list 形式直接抛异常。funasr 1.4.16 的热词偏置调用方式待进一步考证；
  3. 干净直连路径上**基线本身 4/7**：misses 全是英文术语转写形式问题（kraken2→"cr 二/crowken 二"、Q30→"q 三十"、index hopping→"index hoping"）；
  4. **产品决策：转向转写后处理术语纠错**（术语表 + 拼音模糊匹配，引擎无关、可调试、随项目记忆自动更新），热词偏置降级为后续可选项。实验靶子（"测序→测试"劣化）正是纠错模块的标准测试用例。
- 坑 13：funasr 的 vad+spk 管线在 1.4.16 **要求 punc_model**（否则报 Missing punc_model）——已把 punc_ct-transformer（~270MB）加入模型栈，顺带解决转写无标点问题
- 坑 14：**环回采集突然全静音**（RMS=0，SoundPlayer 正常返回、SendKeys 提音量无效）——疑似音频会话/驱动状态问题，需人工看系统托盘音量与输出设备。C2 昨天同流程是通的
- 实验脚本 `spikes/c5_hotwords/hotword_compare.py`（支持 --engine sensevoice|seaco），报告 JSON 在 `data/clips/c5_report_*.json`

## B1 · 项目记忆 RAG 雏形（B 线）

- **2026-10-02 跑通**：jieba 分词 + TF-IDF + 余弦 Top-K（零新增依赖），仿真项目语料 12 段落，跨文件检索命中正确（组会纪要/实验记录/术语表）。脚本 `spikes/b1_rag/rag_demo.py`，语料 `spikes/b1_rag/sample_project/`（3 份 md，模拟真实项目记忆）
- 用途：F1 会前兴趣注入的检索底座 + 厚纪要引用溯源。后续升级路径：TF-IDF → 向量 embedding → 接 agent 记忆

## Review 记录（2026-10-04 · PR #12 review 产物）

- **坑 17 · 自埋 bug 被 review 揪出**：digest 的 terms_hit 循环用 `c["trigger"]` 读 transcript.json 的 clips 条目（实际键为 `evidence`）——第一次 e2e 时报过此错，当时修错了循环且未复跑验证就宣布"digest 通过"（违反"写完必验证"）。本次修复并全链路复跑确认。教训：**修复后必须重跑原始失败用例**。
- **坑 18 · replay 触发时刻用了墙钟**：replay 模式下模型加载耗时会计入 trigger 时间，clip.at 漂移 55s。修复：触发时刻改用会议时间轴（最后进音频的时间戳）。live 模式两者近似等价，不受影响。
- **L2 被队友救活**：Qyc 的 InterestScorer 修复（语料与查询统一 jieba 分词 + 阈值接 config）让 L2 首次真实触发（interest_score=0.33）——原实现语料未分词，中文几乎永不命中。

## 高清补录层（2026-10-05/06 · 用户提案落地）

- **分层捕获定案**：常驻 2fps JPEG 环形（命中前 ~120s）+ 命中后 30fps✕90s 高清补录（HDCapture，mss 高频抓帧→**原始 RGB 帧序列（不做逐帧编码，不阻塞采集线程）**→ffmpeg rawvideo 合成 mp4，用完可删）
- **坑 6 回收**：ffmpeg 一直就在机器上（yt-dlp 附带版，`WinGet/Packages/yt-dlp.FFmpeg/...`）——hd.py 内置该路径探测，PATH 没配也能用。
- 用法：`meetingvar go/record --hd`；命中时自动开录 90s，重复命中忽略（一次一场）；无 ffmpeg 时降级保留帧序列 + `meta.json`（尺寸/帧率/帧数，可事后补合成）
- **坑 26（2026-10-10 修）**：raw 帧曾被写成 `*.png`（内容不是 PNG）→ ffmpeg 实际解码失败，而代码不查返回码**谎报成功并删除唯一证据**。现为 rawvideo 输入 + 返回码/产物校验 + 失败保留帧；mp4 按**实测帧率**合成（抓屏跟不上标称 30fps 时视频时长仍等于真实时长）。代价：raw 帧 1440p 约 12.3MB/帧，90s 标称 30fps 需 ~33GB 临时空间（合成成功后删除）——降分辨率/降帧率/逐帧 JPEG 等缓解方案待产品决策。
- 回放叙事：低帧率铺垫 + 高帧率现场（同体育转播"战术机位常开、超慢动机位按需开"）

## 收尾记录（2026-10-04 晚 · "全部修好"轮）

- PR #13/#12/#14 全部合并，main = MVP 骨架 + review 修复 + 行动项 v2；分支全部清理
- **离线模拟验证通过**（坑 8 的"拔网线"等价测试）：把代理指向死端口强制全部 HTTP 失败，replay 全链路照常出片——端侧零网络依赖坐实，Issue #7 的离线半场关账
- 坑 19 · 双环回端点并发采集会挂死（两个 pa.open loopback 流在 Realtek 驱动上互锁）——**正式版 record 只开默认端点一路**，多端点探测脚本不得进产品代码
- 坑 20 · live record 端到端静音仍未解：默认端点=耳机，SoundPlayer 正常返回但环回零信号。已排除：设备切换、主音量（SendKeys 无效）。剩余嫌疑：耳机端点实际未连接/被独占/Realtek 对耳机插孔的环回渲染缺失。**需人工：拔掉耳机让系统回落扬声器，或对着托盘确认播放设备后再跑一次 `meetingvar record`**

## 本地语义引擎基线（2026-10-05 · Qwen3-4B 全离线）

- **路线**：transformers + ModelScope 直拉（放弃 Ollama：1.58GB 安装器代理限速 2h+，且 Ollama 只是推理壳；meetingvar 环境现成的 torch 2.7.1-cu128 + transformers 就是完整推理栈）
- **模型**：Qwen/Qwen3-4B-Instruct-2507-FP8（ModelScope 4.3GB）→ **FP8 无法在 Windows 推理**（finegrained kernel 依赖 triton，triton 无 Windows 版）→ 写了一次性反量化脚本（block-wise 128×128 dequant）→ **D:\env\models\qwen3-4b-bf16（8.3GB BF16）**
- **服务**：`scripts/serve_local_llm.py`（OpenAI 兼容 /v1/chat/completions @ 127.0.1:11438；device_map=auto，GPU 6.5GiB + 其余 CPU offload；加载 51s；首测推理 145s/短句——CPU offload 是瓶颈）
- **坑 21**：装新依赖（accelerate）后必须重启服务进程——Python site-packages 快照在启动时固定，旧进程照旧报缺依赖（本次排查半小时才定位是双进程同端口+旧进程抢答）
- **坑 22**：nvidia-smi 在 WDDM 下会列大量 N/A 显存的"幽灵进程"（驱动残留），taskkill 全部 Invalid——真实占用看 `Get-Process python*`，我们的 LLM 本身占 6.5GB 是合理配额
- **端到端验证**：`terms --llm` 真模型抽出人物名单（张伟=负责人/李明=执行人/PI=拍板人，含依据），U MI 去污染等新术语，LLM 焦点一句话——**jieba 层完全给不出的特异性**（用户批评 → 架构升级闭环）。代价：CPU offload 下整次抽取约 3-6 分钟，可接受（会前一次性操作）
- 待优化：GPU 配额提到 7.5GiB+（唯一方法是换 AWQ/GPTQ int4 权重，显存装下后全 GPU 推理约 10-20s）；或等 triton-windows 成熟

## 全部修好·第二轮（2026-10-06）

- **① 高清产物入池**：Clip.screen 新增 `hd_clip_dir` 字段，命中时自动写入 HDCapture 会话目录
- **② 回溯窗配置化**：`MEETINGVAR_LOOKBACK` env 可调，默认 300s（原 120s 硬编码）
- **③ 流式化第一步**：增量转写块 30s→8s（`MEETINGVAR_CHUNK` 可调），滑窗重叠 3s 防截句——e2e 重跑 7 句/4 片段正常。真·字级流式（funasr streaming）仍为后续项
- **④ 坑 20 对策落地**：`pick_live_endpoint()` 开录前自动试采各环回端点（每端点独立线程+超时守卫），选 RMS 最大者；全静音回落默认并警告
- **坑 23 · 残留进程持锁**：昨晚探测挂死的真因——残留 python 进程持有环回流时，新进程 `pa.open()` 永不返回（串行也死）。对策=探测线程 join 超时跳过。杀干净残留后单端点采集立刻恢复
- **坑 20 终审**：当前系统所有环回端点 RMS=0 = **没有声音输出在响**（耳机未插/静音/无播放）——不是软件 bug。探测机制会自动选对端点，用户只需确保"开会有声音"
- **⑤ CI 红线**写进 github_guide §6（PR #22 事故的沉淀）

## 本地语义引擎·最终路线（2026-10-06 · int4+llama.cpp）

- **gptqmodel 路线废弃**（坑 24）：transformers 5.x 的 AWQ 推理改走 gptqmodel，而 gptqmodel 拉进的新 torchao 要求 torch≥2.11，与我们为 funasr 锁定的 2.7.1 死锁——**主线性命 > 增强层**，卸载 gptqmodel、pin torchao==0.12.0 后主线恢复（6/6 测试绿）
- **最终路线**：`llama-cpp-python[server]`（PyPI Windows 轮子，**纯 CPU**，坑 25）+ ModelScope `Qwen/Qwen3-4B-GGUF` 的 Q4_K_M（2.5GB）
  - 启动：`python -m llama_cpp.server --model <gguf路径> --port 11439`
  - 实测：`terms --llm` 全流程 **10.6s**（对比 BF16 offload 的 3–6 分钟，快 20–30 倍），连续两跑稳定
- **Qwen3 思考模式**：默认输出 <think>（浪费 token），llm.py 通用剥离 + 抽取 prompt 追加 `/no_think` 软开关——关闭后延迟再减半
- **人物名单质量**：PI=拍板人/张伟=执行人/师弟=负责人，各带依据；两次运行角色细节有小漂移（4B 正常），定位是"供人工确认的候选"
- 全 GPU 提速（18→100+ tok/s）需自行编译 llama.cpp（GGML_CUDA=on，MSVC 在机），列为可选优化
