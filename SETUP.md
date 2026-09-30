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

- 2026-09-30：conda 环境已建，funasr 1.4.16 + torch（CPU 版）已装，`import funasr/torch/modelscope` 通过
- 待办：cu128 torch 后台替换中；SenseVoice + CAM++ 权重下载固化 `models/`；**拔网线验收**
- 模型下载：ModelScope 镜像；权重固化到 `models/`，**验收 = 拔网线跑通**

## C2 · pyaudiowpatch 系统音频环回

（待填）

- 验收 = 采到的是"系统输出"而非麦克风（放视频有声、人不说话）

## C3 · 腾讯会议 / 微信视频实测

（待填）

## C4 · dxcam 低帧率录屏环形缓冲

（待填）

- 本机 Win11 26200 上 Desktop Duplication 可用性待验证；失败退 mss 截屏序列

## C5 · FunASR 热词增强

（待填）

- 验收 = 术语表喂入后纠错可见，记录纠错前后数字（进 PPT）
