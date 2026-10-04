# SETUP 踩坑记录

约定：**谁踩坑谁写**，格式固定为「现象 → 原因 → 解法」，附上最终生效的命令或链接。
两个人别在同一个坑里各摔一次。

## 环境基线

- Python 3.10（conda 独立环境 `meetingvar`；FunASR 生态对 3.11+ 兼容性不稳）
- GPU：**RTX 5060 Laptop 8GB（Blackwell，sm_120）→ torch 必须 ≥2.7 的 cu128 版本**，pypi 默认装的是 CPU 版（`torch.version.cuda == None`），别混
- OS：Windows 11 26200
- **本机没有预装 conda**。已装 Miniconda 26.7.1 到 `D:\miniconda3`（静默参数 `/AddToPath=0`，不污染 PATH）：统一用全路径 `D:\miniconda3\condabin\conda.bat` 和 `D:\miniconda3\envs\meetingvar\python.exe`
- `.condarc` 已配 TUNA 镜像；pip 一律 `-i https://pypi.tuna.tsinghua.edu.cn/simple`
- C 盘仅剩 ~6GB：**模型权重/数据一律放 D 盘**。FunASR/ModelScope 下载前先设 `MODELSCOPE_CACHE=D:\env\modelscope_cache`（默认缓存会灌爆 C 盘用户目录）

## 今日踩坑（2026-09-30）

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
