# SETUP 踩坑记录

约定：**谁踩坑谁写**，格式固定为「现象 → 原因 → 解法」，附上最终生效的命令或链接。
两个人别在同一个坑里各摔一次。

## 环境基线

- Python 3.10（conda 独立环境 `meetingvar`；FunASR 生态对 3.11+ 兼容性不稳）
- GPU / CUDA：＿＿＿（装好后填）
- OS：Windows 11 26200

## C1 · torch + FunASR/SenseVoice + CAM++（离线）

（待填）

- 模型下载：ModelScope 镜像；权重固化到 `models/`，**验收 = 拔网线跑通**
- 遇到的问题：

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
