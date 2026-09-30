# Spike C2 · 系统音频环回

**目标**：pyaudiowpatch 采集 Windows "系统输出"（loopback），录 10 秒存 wav。

- 验收：放视频有声、人不对麦说话，wav 里有内容——证明采的是系统输出而非麦克风
- 产出：最小采集脚本（后续直接搬进 src/meetingvar/capture/）+ 采样率/声道参数记录
- 已知坑位：独占模式占用、声卡驱动差异、默认设备切换（插拔耳机）
